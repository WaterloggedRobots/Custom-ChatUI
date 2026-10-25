using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

/// <summary>
/// Fallback for when the model in Main.unity shows up as missing (for example
/// the FBX was added after the project was first opened and got a new GUID).
/// ChatUI > Add Avatar To Scene drops the newest model in Assets/Models into
/// the open scene and hooks up the face, animation and camera components.
/// </summary>
public static class AvatarSceneBuilder
{
    [MenuItem("ChatUI/Add Avatar To Scene")]
    public static void AddAvatarToScene()
    {
        // Newest model wins, so dropping in an updated FBX just works.
        string path = AssetDatabase.FindAssets("t:Model", new[] { "Assets/Models" })
            .Select(AssetDatabase.GUIDToAssetPath)
            .OrderByDescending(p => File.GetLastWriteTimeUtc(p))
            .FirstOrDefault();
        if (path == null)
        {
            EditorUtility.DisplayDialog("ChatUI", "No model found in Assets/Models. Copy the .fbx there first.", "OK");
            return;
        }

        var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
        var avatar = (GameObject)PrefabUtility.InstantiatePrefab(model);
        Undo.RegisterCreatedObjectUndo(avatar, "Add Avatar");

        var controller = Object.FindObjectOfType<BlendShapeKeyController>();
        if (controller == null)
            controller = Undo.AddComponent<BlendShapeKeyController>(new GameObject("AvatarController"));
        controller.target = avatar.GetComponentsInChildren<SkinnedMeshRenderer>()
            .OrderByDescending(r => r.sharedMesh != null ? r.sharedMesh.blendShapeCount : 0)
            .FirstOrDefault();
        EditorUtility.SetDirty(controller);

        if (controller.GetComponent<AvatarAnimationPlayer>() == null)
            Undo.AddComponent<AvatarAnimationPlayer>(controller.gameObject);

        Camera cam = Camera.main;
        if (cam != null && cam.GetComponent<AvatarCameraFramer>() == null)
            Undo.AddComponent<AvatarCameraFramer>(cam.gameObject);

        Selection.activeGameObject = avatar;
        EditorSceneManager.MarkSceneDirty(avatar.scene);
    }
}
