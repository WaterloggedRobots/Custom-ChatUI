using UnityEditor;
using UnityEngine;

/// <summary>
/// Import fixes for the avatar models in Assets/Models.
///
/// The Blender material drives its color through an emission texture (a small
/// color palette), which Unity's Standard shader turns into a gray, lit
/// material. This swaps it for an unlit, double-sided textured one, matching
/// the flat look in Blender (which also draws back faces, so single-sided
/// hair cards stay visible), and keeps the palette sharp with point filtering.
/// </summary>
public class AvatarModelPostprocessor : AssetPostprocessor
{
    const string ModelsFolder = "Assets/Models/";
    const string TexturedShader = "ChatUI/Unlit Texture Double Sided";

    public override uint GetVersion() => 4;

    void OnPreprocessTexture()
    {
        if (!assetPath.StartsWith(ModelsFolder))
            return;

        var importer = (TextureImporter)assetImporter;
        importer.filterMode = FilterMode.Point;
        importer.mipmapEnabled = false;
        importer.textureCompression = TextureImporterCompression.Uncompressed;
    }

    void OnPostprocessMaterial(Material material)
    {
        if (!assetPath.StartsWith(ModelsFolder))
            return;

        // Only touch materials whose texture is already resolved. During some
        // reimports it isn't yet, and the FBX's base color is black, so any
        // fallback here turns her black; AvatarDoubleSided covers that at runtime.
        Texture texture = GetTexture(material, "_MainTex");
        if (texture == null)
            texture = GetTexture(material, "_EmissionMap");
        if (texture == null)
            return;

        Shader shader = Shader.Find(TexturedShader);
        if (shader == null)
            return;

        material.shader = shader;
        material.mainTexture = texture;
        material.color = Color.white;
    }

    static Texture GetTexture(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetTexture(property) : null;
    }
}
