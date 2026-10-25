using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Sets up the avatar's look at runtime: switches her textured materials to
/// the double-sided toon shader and keeps its color settings in sync with
/// the fields below, so they can be tuned live in Play mode.
///
/// Double-sided matters because the hair is zero-thickness cards that
/// Blender shows from both sides, while Unity's standard shaders cull back
/// faces. The import postprocessor also assigns a double-sided shader, but
/// that depends on import order; this does it on every Play.
/// </summary>
public class AvatarDoubleSided : MonoBehaviour
{
    [Tooltip("Shader to switch textured materials to (ChatUI/Toon Double Sided).")]
    public Shader doubleSidedShader;

    [Tooltip("Texture for materials that came through the import without one (the color palette).")]
    public Texture fallbackTexture;

    [Tooltip("Avatar root. If empty, the root of the face mesh found by the BlendShapeKeyController is used.")]
    public Transform avatarRoot;

    [Header("Look (toon shader)")]
    [Range(0f, 2f)] public float saturation = 1.2f;
    [Range(0.5f, 1.5f)] public float brightness = 1.05f;
    [Range(0.5f, 1.5f)] public float contrast = 1.05f;
    public Color shadowTint = new Color(0.78f, 0.72f, 0.9f);
    [Range(-1f, 1f)] public float shadowThreshold = 0.1f;
    [Range(0.001f, 0.5f)] public float shadowSoftness = 0.06f;
    public Color rimColor = new Color(1f, 0.95f, 0.9f);
    [Range(0f, 1f)] public float rimStrength = 0.25f;
    [Range(0.5f, 8f)] public float rimWidth = 3f;

    [Header("Face")]
    [Tooltip("Shade the face as if it were a sphere around the head, so shadows don't cut across the chin and nose.")]
    [Range(0f, 1f)] public float faceSphereNormals = 1f;
    [Tooltip("How dark the face shadow gets compared to the body (0 = no shadow on the face).")]
    [Range(0f, 1f)] public float faceShadowStrength = 0.5f;
    [Tooltip("Sphere centre relative to the head bone (which sits at the base of the head), in face heights. More negative Z flattens the face shading.")]
    public Vector3 faceSphereOffset = new Vector3(0f, 0.45f, -0.2f);

    readonly List<Material> materials = new List<Material>();
    readonly List<Material> faceMaterials = new List<Material>();
    Transform head;
    float headSize = 0.2f;

    void Start()
    {
        if (doubleSidedShader == null || !doubleSidedShader.isSupported)
            doubleSidedShader = Shader.Find("ChatUI/Toon Double Sided");
        if (doubleSidedShader == null || !doubleSidedShader.isSupported)
            doubleSidedShader = Shader.Find("ChatUI/Unlit Texture Double Sided");
        if (doubleSidedShader == null)
        {
            Debug.LogWarning("AvatarDoubleSided: double-sided shader not found.");
            return;
        }

        var faceController = FindObjectOfType<BlendShapeKeyController>();
        Renderer faceRenderer = faceController != null ? faceController.target : null;
        if (avatarRoot == null && faceRenderer != null)
            avatarRoot = faceRenderer.transform.root;
        if (avatarRoot == null)
            return;

        foreach (Transform t in avatarRoot.GetComponentsInChildren<Transform>())
        {
            if (t.name == "Head")
                head = t;
        }
        if (faceRenderer != null)
            headSize = Mathf.Max(0.01f, faceRenderer.bounds.size.y) / Mathf.Max(0.001f, avatarRoot.lossyScale.y);

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
                if (texture == null)
                    continue;

                m.shader = doubleSidedShader;
                m.mainTexture = texture;
                m.color = Color.white;
                materials.Add(m);
                if (r == faceRenderer)
                    faceMaterials.Add(m);
            }
        }
        ApplyLook();
    }

    void Update()
    {
        ApplyLook();
    }

    void ApplyLook()
    {
        foreach (Material m in materials)
        {
            m.SetFloat("_Saturation", saturation);
            m.SetFloat("_Brightness", brightness);
            m.SetFloat("_Contrast", contrast);
            m.SetColor("_ShadowColor", shadowTint);
            m.SetFloat("_ShadowThreshold", shadowThreshold);
            m.SetFloat("_ShadowSoftness", shadowSoftness);
            m.SetColor("_RimColor", rimColor);
            m.SetFloat("_RimStrength", rimStrength);
            m.SetFloat("_RimPower", rimWidth);

            bool isFace = faceMaterials.Contains(m);
            m.SetFloat("_ShadowStrength", isFace ? faceShadowStrength : 1f);
            m.SetFloat("_SphereNormal", isFace && head != null ? faceSphereNormals : 0f);
        }

        if (head != null && faceMaterials.Count > 0)
        {
            // Offset in the avatar's own frame and scale, sized by the face, so
            // it keeps working when she's moved or resized.
            Vector3 offset = avatarRoot.TransformVector(faceSphereOffset * headSize);
            Vector4 center = head.position + offset;
            foreach (Material m in faceMaterials)
                m.SetVector("_SphereCenter", center);
        }
    }
}
