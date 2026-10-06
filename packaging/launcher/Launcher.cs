// FatimaImageStudio.exe: starts the app with the bundled Python, in this process.
//
// It loads python\python3XX.dll and calls Py_Main("-m studio <args>"), so the app shows up as
// "Fatima Image Studio" (this file's name, icon and version) instead of pythonw.exe. With no arguments
// it runs the tray app ("--tray"); otherwise the arguments are passed to `python -m studio` as given.
// Built by packaging\build.ps1 with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;

static class Launcher
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetDllDirectory(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibrary(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBox(IntPtr owner, string text, string caption, uint type);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int PyMain(int argc, [MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.LPWStr)] string[] argv);

    static int Fail(string message)
    {
        MessageBox(IntPtr.Zero, message + "\n\nReinstalling Fatima Image Studio should fix this.", "Fatima Image Studio", 0x10);
        return 1;
    }

    static int Main(string[] args)
    {
        string exe = System.Reflection.Assembly.GetExecutingAssembly().Location;
        string root = Path.GetDirectoryName(exe);
        string pythonDir = Path.Combine(root, "python");
        string[] dlls = Directory.Exists(pythonDir) ? Directory.GetFiles(pythonDir, "python3??.dll") : new string[0];
        if (dlls.Length == 0)
            return Fail("The bundled Python runtime is missing from:\n" + pythonDir);

        SetDllDirectory(pythonDir);  // python3XX.dll's own dependencies (vcruntime140.dll) live next to it
        IntPtr python = LoadLibrary(dlls[0]);
        if (python == IntPtr.Zero)
            return Fail("Couldn't load " + dlls[0] + " (error " + Marshal.GetLastWin32Error() + ").");
        IntPtr entry = GetProcAddress(python, "Py_Main");
        if (entry == IntPtr.Zero)
            return Fail("The bundled Python runtime is damaged (no Py_Main).");

        var argv = new List<string> { exe, "-m", "studio" };
        if (args.Length == 0)
            argv.Add("--tray");
        else
            argv.AddRange(args);
        Environment.CurrentDirectory = root;
        var main = (PyMain)Marshal.GetDelegateForFunctionPointer(entry, typeof(PyMain));
        return main(argv.Count, argv.ToArray());
    }
}
