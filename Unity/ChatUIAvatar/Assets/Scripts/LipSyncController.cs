using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// The shape keys used for lip sync while she talks, one per mouth shape
/// (viseme). Nothing drives it automatically yet: once ChatUI's text to
/// speech is linked, it (or AvatarApiServer's /api/lipsync) calls SetViseme
/// with the current mouth shape and how open it is.
///
/// Lip sync is added on top of the motion set and panel shapes, so she can
/// talk while smiling.
/// </summary>
public class LipSyncController : MonoBehaviour
{
    [Serializable]
    public class VisemeShape
    {
        [Tooltip("Mouth shape name used by callers, e.g. A, I, U, E, O.")]
        public string viseme;
        [ShapeKeyName] public string shapeKey;
        [Tooltip("Weight when the mouth shape is fully on.")]
        [Range(0f, 100f)] public float maxWeight = 100f;

        public VisemeShape() { }

        public VisemeShape(string viseme, string shapeKey, float maxWeight = 100f)
        {
            this.viseme = viseme;
            this.shapeKey = shapeKey;
            this.maxWeight = maxWeight;
        }
    }

    public List<VisemeShape> visemes = new List<VisemeShape>
    {
        new VisemeShape("A", "aa"),
        new VisemeShape("I", "ih"),
        new VisemeShape("U", "ou"),
        new VisemeShape("E", "ee"),
        new VisemeShape("O", "oh"),
    };

    [Tooltip("Seconds without an update before the mouth closes on its own, so a lost connection doesn't leave it open. 0 never.")]
    [Min(0f)] public float autoCloseAfter = 0.5f;

    BlendShapeKeyController face;
    readonly Dictionary<string, float> amounts = new Dictionary<string, float>(StringComparer.OrdinalIgnoreCase);
    float lastUpdate;

    /// <summary>True while any mouth shape is open.</summary>
    public bool Speaking => amounts.Count > 0;

    void Start()
    {
        face = FindObjectOfType<BlendShapeKeyController>();
    }

    void Update()
    {
        if (Speaking && autoCloseAfter > 0f && Time.time - lastUpdate > autoCloseAfter)
            Clear();
    }

    /// <summary>Opens one mouth shape (amount 0-1) and closes the others.</summary>
    public bool SetViseme(string viseme, float amount)
    {
        if (Find(viseme) == null)
            return false;
        amounts.Clear();
        if (amount > 0f)
            amounts[viseme] = Mathf.Clamp01(amount);
        Apply();
        return true;
    }

    /// <summary>Sets several mouth shapes at once (amounts 0-1); unlisted ones close.</summary>
    public void SetVisemes(IEnumerable<KeyValuePair<string, float>> values)
    {
        amounts.Clear();
        foreach (var pair in values)
        {
            if (Find(pair.Key) != null && pair.Value > 0f)
                amounts[pair.Key] = Mathf.Clamp01(pair.Value);
        }
        Apply();
    }

    public void Clear()
    {
        amounts.Clear();
        Apply();
    }

    public VisemeShape Find(string viseme)
    {
        return visemes.Find(v => string.Equals(v.viseme, viseme, StringComparison.OrdinalIgnoreCase));
    }

    void Apply()
    {
        lastUpdate = Time.time;
        if (face == null)
            return;

        face.ClearLayer(BlendShapeKeyController.Layer.LipSync);
        foreach (var pair in amounts)
        {
            VisemeShape v = Find(pair.Key);
            int index = face.FindShape(v.shapeKey);
            if (index >= 0)
                face.SetLayerWeight(BlendShapeKeyController.Layer.LipSync, index, v.maxWeight * pair.Value);
        }
    }
}
