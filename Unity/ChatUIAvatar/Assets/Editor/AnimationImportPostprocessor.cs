using System.IO;
using System.Linq;
using UnityEditor;

/// <summary>
/// Import settings for the animation FBXs in Assets/Resources/Animations.
///
/// Every file is imported as a Humanoid rig, so clips retarget onto the
/// avatar whatever skeleton they were made on: Mixamo's own rig
/// (mixamorig:Hips...) as well as Miorine's. Each file keeps a single clip
/// named after the file, so AvatarAnimationPlayer can list them by name, and
/// root motion is baked into the pose so she stays where she's placed.
/// </summary>
public class AnimationImportPostprocessor : AssetPostprocessor
{
    const string AnimationsFolder = "Assets/Resources/Animations/";

    // Takes that are just the avatar's own Blender idle, carried along in
    // files that were exported from her model.
    static readonly string[] IgnoredTakes = { "miorine idle" };

    public override uint GetVersion() => 1;

    void OnPreprocessModel()
    {
        if (!assetPath.StartsWith(AnimationsFolder))
            return;

        var importer = (ModelImporter)assetImporter;
        importer.animationType = ModelImporterAnimationType.Human;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.materialImportMode = ModelImporterMaterialImportMode.None;
        importer.importBlendShapes = false;
        importer.importAnimation = true;
    }

    void OnPreprocessAnimation()
    {
        if (!assetPath.StartsWith(AnimationsFolder))
            return;

        var importer = (ModelImporter)assetImporter;
        ModelImporterClipAnimation[] takes = importer.defaultClipAnimations;
        if (takes.Length == 0)
            return;

        ModelImporterClipAnimation clip =
            takes.FirstOrDefault(t => t.takeName == "mixamo.com")
            ?? takes.FirstOrDefault(t => !IgnoredTakes.Any(ignored => t.takeName.EndsWith(ignored)))
            ?? takes[0];

        clip.name = Path.GetFileNameWithoutExtension(assetPath);
        clip.lockRootRotation = true;
        clip.lockRootHeightY = true;
        clip.lockRootPositionXZ = true;
        clip.keepOriginalOrientation = true;
        clip.keepOriginalPositionY = true;
        clip.keepOriginalPositionXZ = true;
        importer.clipAnimations = new[] { clip };
    }
}
