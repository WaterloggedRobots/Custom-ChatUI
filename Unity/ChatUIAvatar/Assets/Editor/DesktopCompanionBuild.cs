using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>
/// Player settings and a one-click build for the transparent desktop
/// companion (see DesktopCompanionWindow). Transparency needs DirectX 11 with
/// the DXGI flip model swapchain turned off, and a windowed player.
/// </summary>
public static class DesktopCompanionBuild
{
    const string OutputFolder = "Builds/DesktopCompanion";

    [MenuItem("ChatUI/Apply Desktop Companion Player Settings")]
    public static void ApplyPlayerSettings()
    {
        PlayerSettings.useFlipModelSwapchain = false;
        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.defaultIsNativeResolution = true;
        PlayerSettings.resizableWindow = false;
        PlayerSettings.allowFullscreenSwitch = false;
        PlayerSettings.runInBackground = true;
        PlayerSettings.visibleInBackground = true;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64, new[] { GraphicsDeviceType.Direct3D11 });
        AssetDatabase.SaveAssets();
        Debug.Log("Desktop companion player settings applied.");
    }

    [MenuItem("ChatUI/Build Desktop Companion")]
    public static void Build()
    {
        ApplyPlayerSettings();

        string exe = Path.Combine(OutputFolder, PlayerSettings.productName + ".exe");
        var options = new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = exe,
            target = BuildTarget.StandaloneWindows64,
            options = BuildOptions.None,
        };

        BuildReport report = BuildPipeline.BuildPlayer(options);
        if (report.summary.result == BuildResult.Succeeded)
        {
            Debug.Log($"Desktop companion built: {Path.GetFullPath(exe)}");
            EditorUtility.RevealInFinder(exe);
        }
        else
        {
            Debug.LogError($"Desktop companion build failed: {report.summary.result}");
        }
    }
}
