using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Win32;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swpublished;

namespace SolidWorksMcpAddin;

[ComVisible(true)]
[Guid(AddinGuid)]
[ProgId("SolidWorksMcpAddin.Connect")]
public sealed class SwAddin : ISwAddin
{
    private const string AddinGuid = "5F9A7BAA-CE9E-4D34-8B88-5E5DCA04C6B6";
    private const string PanelUrl = "http://127.0.0.1:8766/panel";
    private dynamic? _swApp;
    private dynamic? _taskPane;
    private PanelControl? _control;
    private Process? _uiProcess;

    public bool ConnectToSW(object ThisSW, int Cookie)
    {
        _swApp = ThisSW;
        try
        {
            _swApp.SetAddinCallbackInfo(0, this, Cookie);
            EnsureUiServer();
            _control = new PanelControl(PanelUrl);
            _taskPane = _swApp.CreateTaskpaneView2("", "SolidWorks MCP");
            _taskPane.DisplayWindowFromHandlex64(_control.Handle.ToInt64());
            return true;
        }
        catch (Exception ex)
        {
            MessageBox.Show("SolidWorks MCP 面板加载失败: " + ex.Message, "SolidWorks MCP", MessageBoxButtons.OK, MessageBoxIcon.Error);
            DisconnectFromSW();
            return false;
        }
    }

    public bool DisconnectFromSW()
    {
        try
        {
            _taskPane?.DeleteView();
        }
        catch { /* SolidWorks may already be shutting down. */ }
        _taskPane = null;
        _control?.Dispose();
        _control = null;
        _swApp = null;
        // The UI process is intentionally left alive: it owns no SolidWorks
        // document and can serve a panel reopened during the same session.
        _uiProcess = null;
        return true;
    }

    private void EnsureUiServer()
    {
        if (IsUiServerReady()) return;

        string? root = Environment.GetEnvironmentVariable("SOLIDWORKS_MCP_ROOT");
        if (string.IsNullOrWhiteSpace(root))
        {
            var cursor = new DirectoryInfo(Path.GetDirectoryName(typeof(SwAddin).Assembly.Location) ?? "");
            for (var i = 0; i < 6 && cursor != null; i++, cursor = cursor.Parent)
            {
                if (File.Exists(Path.Combine(cursor.FullName, "run-panel.ps1"))) { root = cursor.FullName; break; }
            }
        }
        string script = Path.Combine(root ?? "", "run-panel.ps1");
        if (!File.Exists(script)) return;

        _uiProcess = Process.Start(new ProcessStartInfo
        {
            FileName = "powershell.exe",
            Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"",
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        });

        // The launcher backgrounds Uvicorn. Wait briefly so the normal path
        // is ready before the Task Pane browser navigates; PanelControl keeps
        // polling as a second guard for slower first starts.
        for (var attempt = 0; attempt < 20 && !IsUiServerReady(); attempt++)
        {
            System.Threading.Thread.Sleep(250);
        }
    }

    private static bool IsUiServerReady()
    {
        try
        {
            // Keep this probe independent of SolidWorks COM. It is only a
            // readiness check for the local Uvicorn bridge.
            var request = (HttpWebRequest)WebRequest.Create(
                "http://127.0.0.1:8766/api/health");
            request.Timeout = 500;
            request.ReadWriteTimeout = 500;
            using var response = (HttpWebResponse)request.GetResponse();
            return response.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    [ComRegisterFunction]
    public static void RegisterFunction(Type type)
    {
        using RegistryKey? addin = Registry.LocalMachine.CreateSubKey(
            "SOFTWARE\\SolidWorks\\Addins\\{" + AddinGuid + "}");
        addin?.SetValue(null, 0, RegistryValueKind.DWord);
        addin?.SetValue("Title", "SolidWorks MCP", RegistryValueKind.String);
        addin?.SetValue("Description", "Embedded Task Pane for the SolidWorks MCP server", RegistryValueKind.String);
        using RegistryKey? startup = Registry.CurrentUser.CreateSubKey(
            "Software\\SolidWorks\\AddInsStartup\\{" + AddinGuid + "}");
        startup?.SetValue(null, 1, RegistryValueKind.DWord);
    }

    [ComUnregisterFunction]
    public static void UnregisterFunction(Type type)
    {
        try { Registry.LocalMachine.DeleteSubKeyTree("SOFTWARE\\SolidWorks\\Addins\\{" + AddinGuid + "}", false); } catch { }
        try { Registry.CurrentUser.DeleteSubKeyTree("Software\\SolidWorks\\AddInsStartup\\{" + AddinGuid + "}", false); } catch { }
    }
}
