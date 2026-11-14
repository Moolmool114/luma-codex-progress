using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class Launcher {
    [STAThread]
    private static void Main() {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        try {
            var config=new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(Path.Combine(root,"launch-config.json")));
            string python=config["python"];
            if(!File.Exists(python))throw new Exception("找不到 Python 运行时。请重新运行 prepare.py。");
            var start=new ProcessStartInfo(python,Quote(Path.Combine(root,"app.py"))+" --codex-home "+Quote(config["codexHome"]));
            start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;
            start.WindowStyle=ProcessWindowStyle.Hidden;
            Process.Start(start);
        } catch(Exception error) {MessageBox.Show(error.Message,"Luma · 启动失败",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    }
    private static string Quote(string value){return "\""+value.Replace("\"","\\\"")+"\"";}
}
