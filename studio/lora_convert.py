"""Make FLUX.2 LoRAs fully usable by stable-diffusion.cpp.

FLUX.2 LoRAs (diffusers / ComfyUI exports) name some layers in a way the engine doesn't map yet, so it
silently skips them — typically half the LoRA (feed-forward + single-block layers). This rewrites those
layers into the FLUX.1-style diffusers names the engine does map:

  transformer_blocks.N.ff.linear_in / linear_out           -> ff.net.0.proj / ff.net.2  (img_mlp.0 / .2)
  transformer_blocks.N.ff_context.linear_in / linear_out   -> ff_context.net.0.proj / .net.2
  single_transformer_blocks.N.attn.to_qkv_mlp_proj (fused) -> to_q / to_k / to_v / proj_mlp
      (the up-matrix is split by rows, the down-matrix shared — mathematically the same update)

Only tensor names and row slices change; no weights are re-computed, so it's exact and fast.
"""
import json
import re
import struct
from pathlib import Path

DTYPE_SIZE = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1, "BOOL": 1}
RENAMES = [
    (re.compile(r"^(?P<p>(?:.*\.)?transformer_blocks\.\d+\.)ff\.linear_in\."), r"\g<p>ff.net.0.proj."),
    (re.compile(r"^(?P<p>(?:.*\.)?transformer_blocks\.\d+\.)ff\.linear_out\."), r"\g<p>ff.net.2."),
    (re.compile(r"^(?P<p>(?:.*\.)?transformer_blocks\.\d+\.)ff_context\.linear_in\."), r"\g<p>ff_context.net.0.proj."),
    (re.compile(r"^(?P<p>(?:.*\.)?transformer_blocks\.\d+\.)ff_context\.linear_out\."), r"\g<p>ff_context.net.2."),
]
PEFT = re.compile(r"\.(lora_[AB]|lora_down|lora_up)\.default\.weight$")
FUSED = re.compile(r"^(?P<p>(?:.*\.)?single_transformer_blocks\.\d+\.)attn\.to_qkv_mlp_proj\.(?P<s>lora_[AB]|lora_down|lora_up)\.weight$")


def read(path: Path) -> tuple[dict, dict, bytes]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
        body = f.read()
    meta = header.pop("__metadata__", {}) or {}
    return header, meta, body


def header_only(path: Path) -> tuple[dict, dict]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    return header, header.pop("__metadata__", {}) or {}


def needs_conversion(header: dict) -> bool:
    return any(FUSED.match(k) or PEFT.search(k) or any(p.match(k) for p, _ in RENAMES) for k in header)


def convert(src: Path, dst: Path, hidden: int = 3072) -> int:
    """Write a converted copy; returns how many tensors were renamed or split."""
    header, meta, body = read(src)
    out: list[tuple[str, dict, bytes]] = []
    changed = 0
    for name, info in header.items():
        start, end = info["data_offsets"]
        data = body[start:end]
        if PEFT.search(name):  # PEFT export: drop the adapter name
            changed += 1
            name = PEFT.sub(r".\1.weight", name)
        m = FUSED.match(name)
        if m:
            changed += 1
            prefix, side = m["p"], m["s"]
            parts = ["attn.to_q", "attn.to_k", "attn.to_v", "proj_mlp"]
            if side in ("lora_A", "lora_down"):  # shared input projection: copy it to each part
                for part in parts:
                    out.append((f"{prefix}{part}.{side}.weight", info, data))
            else:  # output rows: [q | k | v | mlp] -> four slices
                rows, rank = info["shape"]
                row_bytes = rank * DTYPE_SIZE[info["dtype"]]
                bounds = [0, hidden, 2 * hidden, 3 * hidden, rows]
                for part, a, b in zip(parts, bounds, bounds[1:]):
                    out.append((f"{prefix}{part}.{side}.weight", info | {"shape": [b - a, rank]},
                                data[a * row_bytes:b * row_bytes]))
            continue
        new = name
        for pattern, repl in RENAMES:
            new = pattern.sub(repl, new)
        changed += new != name
        out.append((new, info, data))

    new_header, chunks, offset = {}, [], 0
    if meta:
        new_header["__metadata__"] = {k: str(v) for k, v in meta.items()}
    for name, info, data in out:
        new_header[name] = {"dtype": info["dtype"], "shape": info["shape"], "data_offsets": [offset, offset + len(data)]}
        chunks.append(data)
        offset += len(data)
    raw = json.dumps(new_header, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 8)
    with open(dst, "wb") as f:
        f.write(struct.pack("<Q", len(raw)))
        f.write(raw)
        for c in chunks:
            f.write(c)
    return changed


def detect_family(header: dict) -> str | None:
    """Which base model a LoRA was trained for, from its layer names and widths.

    SD 1.5 / SDXL: U-Net layers (down/input blocks, lora_te…); the cross-attention key input is 768 for
      SD 1.5 and 2048 for SDXL (which also has a second text encoder, te2).
    Qwen-Image: img_mlp / txt_mlp layers outside FLUX's double/single blocks; hidden 3072, or 4096 for 2.1.
    FLUX.2: klein 4B 3072 with 9216 / 18432 / 27648; klein 9B 4096 with 16384 / 24576 / 36864; dev 6144.
    Z-Image: 3840.  FLUX.1 (schnell, dev, Kontext): 3072 in double/single blocks; Chroma adds a
      distilled_guidance_layer.
    """
    names = [k for k, v in header.items() if isinstance(v, dict)]
    low = [k.lower() for k in names]
    has = lambda *parts: any(any(p in k for p in parts) for k in low)
    counts: dict[int, int] = {}
    for k in names:
        for d in header[k].get("shape", []):
            if d >= 1024:
                counts[d] = counts.get(d, 0) + 1

    if has("lora_unet_down_blocks", "lora_unet_input_blocks", "lora_unet_mid_block", "unet.down_blocks",
           "unet.mid_block", "lora_te_", "lora_te1_", "lora_te2_") or (has("down_blocks", "input_blocks") and not has("transformer_blocks.")):
        if has("lora_te2_", "text_encoder_2", "te2."):
            return "sdxl"
        for k in names:  # cross-attention key/value input width: 768 SD 1.5, 2048 SDXL
            kl = k.lower()
            if "attn2" in kl and ("to_k" in kl or "to_v" in kl) and ("lora_down" in kl or "lora_a" in kl):
                width = header[k]["shape"][-1]
                return {768: "sd15", 2048: "sdxl"}.get(width)
        return "sdxl" if 2048 in counts else "sd15"
    if not counts:
        return None
    hidden = max(counts, key=counts.get)
    flux_blocks = has("double_blocks", "single_blocks", "single_transformer_blocks")
    blocks: dict[str, int] = {}  # how many blocks of each kind the LoRA touches
    for k in low:
        for m in BLOCK.finditer(k):
            kind = "single" if m[1].startswith("single") else "double"
            blocks[kind] = max(blocks.get(kind, 0), int(m[2]) + 1)
    # Qwen-Image: its own MLP names, or many transformer blocks and no FLUX single blocks (attention-only LoRAs)
    if (has("img_mlp", "txt_mlp") and not flux_blocks) or (blocks.get("double", 0) > 20 and "single" not in blocks):
        return {3072: "qwen-image", 4096: "qwen-image-2.1"}.get(hidden)
    if hidden == 3072:  # FLUX.1 (19 double / 38 single blocks) or FLUX.2 klein 4B (5 / 20)
        if blocks.get("double", 0) > 5 or blocks.get("single", 0) > 20:
            return "chroma" if has("distilled_guidance_layer") else "flux1"
        if counts.keys() & {18432, 27648}:
            return "flux2-klein-4b"
    if hidden == 4096 and counts.keys() & {16384, 24576, 36864}:
        return "flux2-klein-9b"
    if hidden == 6144:
        return "flux2-dev"
    if hidden == 3840:
        return "z-image"
    if hidden == 3072 and (flux_blocks or counts.keys() & {12288, 21504}):
        return "chroma" if has("distilled_guidance_layer") else "flux1"
    return None


BLOCK = re.compile(r"(single_transformer_blocks|single_blocks|double_blocks|(?<!single_)transformer_blocks)[._](\d+)")


# Hidden size per FLUX.2 family, for splitting their fused single-block layer (see convert()).
FUSED_HIDDEN = {"flux2-klein-4b": 3072, "flux2-klein-9b": 4096, "flux2-dev": 6144}
