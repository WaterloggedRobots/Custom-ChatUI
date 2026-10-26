using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.SceneManagement;

/// <summary>
/// Stores the animations inside the avatar's FBX on every AvatarAnimationPlayer
/// while a scene is built, since a player build can't look them up from the
/// asset database the way the editor does.
/// </summary>
public class ModelClipsBuildProcessor : IProcessSceneWithReport
{
    public int callbackOrder => 0;

    public void OnProcessScene(Scene scene, BuildReport report)
    {
        foreach (GameObject root in scene.GetRootGameObjects())
        {
            foreach (AvatarAnimationPlayer player in root.GetComponentsInChildren<AvatarAnimationPlayer>(true))
                player.RefreshModelClips();
        }
    }
}
