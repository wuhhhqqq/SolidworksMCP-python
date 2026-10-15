using System;
using System.Drawing;
using System.Net;
using System.Windows.Forms;

namespace SolidWorksMcpAddin;

internal sealed class PanelControl : UserControl
{
    private readonly WebBrowser _browser = new();
    private readonly System.Windows.Forms.Timer _readyTimer = new();
    private readonly string _url;
    private readonly string _healthUrl;
    private int _remainingHealthChecks = 30;

    public PanelControl(string url)
    {
        _url = url;
        // This endpoint only confirms that Uvicorn is listening. The panel
        // page performs the separate SolidWorks COM attach handshake.
        _healthUrl = new Uri(new Uri(url), "/api/health").ToString();
        Dock = DockStyle.Fill;
        MinimumSize = new Size(260, 320);
        _browser.Dock = DockStyle.Fill;
        _browser.ScriptErrorsSuppressed = true;
        _browser.AllowWebBrowserDrop = false;
        _browser.IsWebBrowserContextMenuEnabled = false;
        _browser.WebBrowserShortcutsEnabled = true;
        Controls.Add(_browser);
        _readyTimer.Interval = 500;
        _readyTimer.Tick += (_, _) => NavigateWhenBridgeReady();
        NavigateWhenBridgeReady();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _readyTimer.Stop();
            _readyTimer.Dispose();
        }
        base.Dispose(disposing);
    }

    private void NavigateWhenBridgeReady()
    {
        if (IsBridgeReady())
        {
            _readyTimer.Stop();
            _browser.Navigate(_url);
            return;
        }

        if (_remainingHealthChecks-- <= 0)
        {
            _readyTimer.Stop();
            return;
        }
        if (!_readyTimer.Enabled) _readyTimer.Start();
    }

    private bool IsBridgeReady()
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(_healthUrl);
            request.Timeout = 300;
            request.ReadWriteTimeout = 300;
            using var response = (HttpWebResponse)request.GetResponse();
            return response.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }
}
