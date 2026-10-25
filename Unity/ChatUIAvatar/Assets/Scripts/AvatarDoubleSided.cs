using UnityEngine;

/// <summary>
/// Makes the avatar's textured materials render both sides at runtime.
///
/// The hair is zero-thickness cards that Blender shows from both sides, but
/// Unity's standard shaders cull back faces, so the inside of the hair goes
/// missing. The import postprocessor also tries to assign the double-sided
/// shader, but that depends on import order; this does it on every Play.
/// </summary>
public class AvatarDoubleSided : MonoBehaviour
{
    [Tooltip("Double-sided shader to switch textured materials to.")]
    public Shader doubleSidedShader;

    [Tooltip("Texture for materials that came through the import without one (the color palette).")]
    public Texture fallbackTexture;

    [Tooltip("Avatar root. If empty, the root of the face mesh found by the BlendShapeKeyController is used.")]
    public Transform avatarRoot;

    void Start()
    {
        if (doubleSidedShader == null)
            doubleSidedShader = Shader.Find("ChatUI/Unlit Texture Double Sided");
        if (doubleSidedShader == null)
        {
            Debug.LogWarning("AvatarDoubleSided: double-sided shader not found.");
            return;
        }

        if (avatarRoot == null)
        {
            var face = FindObjectOfType<BlendShapeKeyController>();
            if (face != null && face.target != null)
                avatarRoot = face.target.transform.root;
        }
        if (avatarRoot == null)
            return;

        foreach (Renderer r in avatarRoot.GetComponentsInChildren<Renderer>())
        {
            // .materials gives per-renderer copies, so imported assets stay untouched.
            foreach (Material m in r.materials)
            {
                Texture texture = m.HasProperty("_MainTex") ? m.mainTexture : null;
                if (texture == null && m.HasProperty("_EmissionMap"))
                    texture = m.GetTexture("_EmissionMap");
                if (texture == null && !m.name.StartsWith("outline"))
                    texture = fallbackTexture;
                if (texture == null || (m.shader == doubleSidedShader && m.mainTexture == texture))
                    continue;

                m.shader = doubleSidedShader;
                m.mainTexture = texture;
                m.color = Color.white;
            }
        }
    }
}
