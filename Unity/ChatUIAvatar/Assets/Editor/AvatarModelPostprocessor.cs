using System.IO;
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
    const string PaletteTexture = "miorine tx.png";

    const string TexturedShader = "ChatUI/Unlit Texture Double Sided";

    public override uint GetVersion() => 2;

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

        Texture texture = GetTexture(material, "_MainTex") ?? GetTexture(material, "_EmissionMap");
        if (texture == null && material.name.StartsWith("colors"))
        {
            string palettePath = Path.Combine(Path.GetDirectoryName(assetPath), PaletteTexture).Replace('\\', '/');
            texture = AssetDatabase.LoadAssetAtPath<Texture2D>(palettePath);
        }

        if (texture != null)
        {
            material.shader = Shader.Find(TexturedShader) ?? Shader.Find("Unlit/Texture");
            material.mainTexture = texture;
            return;
        }

        // Untextured materials (the inverted-hull outline) keep their flat color.
        Color emission = material.HasProperty("_EmissionColor") ? material.GetColor("_EmissionColor") : Color.black;
        Color color = material.HasProperty("_Color") ? material.color : Color.black;
        material.shader = Shader.Find("Unlit/Color");
        material.color = emission.maxColorComponent > 0f ? emission : color;
    }

    static Texture GetTexture(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetTexture(property) : null;
    }
}
