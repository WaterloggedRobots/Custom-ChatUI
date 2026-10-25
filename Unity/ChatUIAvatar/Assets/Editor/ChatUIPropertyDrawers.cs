using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

/// <summary>
/// Dropdowns for [AnimationName] and [ShapeKeyName] string fields, so motion
/// sets and lip sync shapes are picked from what's actually in the project
/// instead of typed. A value that isn't in the list (e.g. a renamed shape key)
/// is kept and shown as missing.
/// </summary>
static class ChatUIDropdown
{
    public static void Draw(Rect position, SerializedProperty property, GUIContent label, string[] options, string emptyLabel)
    {
        if (property.propertyType != SerializedPropertyType.String || options == null)
        {
            EditorGUI.PropertyField(position, property, label);
            return;
        }

        string value = property.stringValue ?? "";
        var shown = new System.Collections.Generic.List<string> { emptyLabel };
        shown.AddRange(options);
        int index = string.IsNullOrEmpty(value) ? 0 : shown.IndexOf(value);
        if (index < 0)
        {
            shown.Add(value + " (missing)");
            index = shown.Count - 1;
        }

        EditorGUI.BeginProperty(position, label, property);
        int picked = EditorGUI.Popup(position, label.text, index, shown.ToArray());
        if (picked != index)
            property.stringValue = picked == 0 ? "" : picked <= options.Length ? options[picked - 1] : value;
        EditorGUI.EndProperty();
    }
}

[CustomPropertyDrawer(typeof(AnimationNameAttribute))]
public class AnimationNameDrawer : PropertyDrawer
{
    const string Folder = "Assets/Resources/Animations";

    public override void OnGUI(Rect position, SerializedProperty property, GUIContent label)
    {
        string[] names = AssetDatabase.IsValidFolder(Folder)
            ? AssetDatabase.FindAssets("t:Model", new[] { Folder })
                .Select(guid => Path.GetFileNameWithoutExtension(AssetDatabase.GUIDToAssetPath(guid)))
                .Distinct()
                .OrderBy(n => n)
                .ToArray()
            : null;
        ChatUIDropdown.Draw(position, property, label, names, "(keep current)");
    }
}

[CustomPropertyDrawer(typeof(ShapeKeyNameAttribute))]
public class ShapeKeyNameDrawer : PropertyDrawer
{
    public override void OnGUI(Rect position, SerializedProperty property, GUIContent label)
    {
        ChatUIDropdown.Draw(position, property, label, FaceShapeNames(), "(none)");
    }

    static string[] FaceShapeNames()
    {
        SkinnedMeshRenderer face = null;
        var controller = Object.FindObjectOfType<BlendShapeKeyController>();
        if (controller != null)
            face = controller.target;
        if (face == null)
        {
            face = Object.FindObjectsOfType<SkinnedMeshRenderer>()
                .Where(r => r.sharedMesh != null)
                .OrderByDescending(r => r.sharedMesh.blendShapeCount)
                .FirstOrDefault();
        }
        if (face == null || face.sharedMesh == null)
            return null;

        Mesh mesh = face.sharedMesh;
        return Enumerable.Range(0, mesh.blendShapeCount).Select(mesh.GetBlendShapeName).ToArray();
    }
}
