using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Drives blend shapes (shape keys) on the avatar's face from the keyboard.
///
/// The on-screen panel lists every shape key on the face mesh; click one to
/// toggle it. The panel collapses, scrolls and can be filtered by name.
/// Number keys 1-0 (top row or numpad) are shortcuts for the shapes bound to
/// them, and Shift + number keys a second bank.
/// Minus / Equals step through every blend shape on the mesh one at a time,
/// which is handy for finding names to bind. Backspace clears everything.
///
/// Later the same SetExpression / SetWeight calls can be driven by the
/// ChatUI server instead of the keyboard.
/// </summary>
public class BlendShapeKeyController : MonoBehaviour
{
    [Serializable]
    public class Binding
    {
        public KeyCode key;
        public KeyCode altKey;
        [Tooltip("Only fires while Shift is held (and the plain key only fires without Shift).")]
        public bool shift;
        [Tooltip("Blend shape name as it appears on the SkinnedMeshRenderer.")]
        public string blendShapeName;

        [NonSerialized] public int index = -1;

        public Binding(KeyCode key, KeyCode altKey, string blendShapeName, bool shift = false)
        {
            this.key = key;
            this.altKey = altKey;
            this.blendShapeName = blendShapeName;
            this.shift = shift;
        }
    }

    [Tooltip("Face mesh. If empty, the child (or scene) SkinnedMeshRenderer with the most blend shapes is used.")]
    public SkinnedMeshRenderer target;

    [Tooltip("On: a key press toggles its shape. Off: the shape is on only while the key is held.")]
    public bool toggleMode = true;

    [Tooltip("Turning a shape on turns the other bound shapes off.")]
    public bool exclusive = true;

    [Tooltip("Blend shape weight change per second (weights run 0-100).")]
    public float transitionSpeed = 600f;

    [Range(0f, 100f)] public float onWeight = 100f;

    public bool showOverlay = true;

    public KeyCode resetKey = KeyCode.Backspace;
    public KeyCode browsePrevKey = KeyCode.Minus;
    public KeyCode browseNextKey = KeyCode.Equals;

    public List<Binding> bindings = new List<Binding>
    {
        new Binding(KeyCode.Alpha1, KeyCode.Keypad1, "blink"),
        new Binding(KeyCode.Alpha2, KeyCode.Keypad2, "happy"),
        new Binding(KeyCode.Alpha3, KeyCode.Keypad3, "angry"),
        new Binding(KeyCode.Alpha4, KeyCode.Keypad4, "sad"),
        new Binding(KeyCode.Alpha5, KeyCode.Keypad5, "surprised"),
        new Binding(KeyCode.Alpha6, KeyCode.Keypad6, "aa"),
        new Binding(KeyCode.Alpha7, KeyCode.Keypad7, "ih"),
        new Binding(KeyCode.Alpha8, KeyCode.Keypad8, "ou"),
        new Binding(KeyCode.Alpha9, KeyCode.Keypad9, "ee"),
        new Binding(KeyCode.Alpha0, KeyCode.Keypad0, "oh"),
        new Binding(KeyCode.Alpha1, KeyCode.Keypad1, "joy", true),
        new Binding(KeyCode.Alpha2, KeyCode.Keypad2, "smug", true),
        new Binding(KeyCode.Alpha3, KeyCode.Keypad3, "pout", true),
        new Binding(KeyCode.Alpha4, KeyCode.Keypad4, "cry", true),
        new Binding(KeyCode.Alpha5, KeyCode.Keypad5, "sleepy", true),
        new Binding(KeyCode.Alpha6, KeyCode.Keypad6, "embarrassed", true),
        new Binding(KeyCode.Alpha7, KeyCode.Keypad7, "shocked", true),
        new Binding(KeyCode.Alpha8, KeyCode.Keypad8, "annoyed", true),
        new Binding(KeyCode.Alpha9, KeyCode.Keypad9, "grin", true),
        new Binding(KeyCode.Alpha0, KeyCode.Keypad0, "furious", true),
    };

    float[] targetWeights;
    float[] currentWeights;
    int browseIndex = -1;
    string[] shapeNames;
    readonly Dictionary<int, string> keyLabels = new Dictionary<int, string>();
    bool panelExpanded = true;
    Vector2 scroll;
    string filter = "";
    GUIStyle rowStyle;
    GUIStyle activeRowStyle;

    // Resolve the target in Awake so other components can read it in Start.
    void Awake()
    {
        if (target == null)
            target = FindFaceRenderer();
    }

    void Start()
    {
        if (target == null || target.sharedMesh == null)
        {
            Debug.LogWarning("BlendShapeKeyController: no SkinnedMeshRenderer with blend shapes found.");
            enabled = false;
            return;
        }

        Mesh mesh = target.sharedMesh;
        targetWeights = new float[mesh.blendShapeCount];
        currentWeights = new float[mesh.blendShapeCount];

        // Read the list from the mesh, so a re-exported model with more shape
        // keys shows them all without any changes here.
        shapeNames = new string[mesh.blendShapeCount];
        for (int i = 0; i < shapeNames.Length; i++)
            shapeNames[i] = mesh.GetBlendShapeName(i);
        Debug.Log($"BlendShapeKeyController: {target.name} has {shapeNames.Length} blend shapes: {string.Join(", ", shapeNames)}");

        foreach (Binding b in bindings)
        {
            b.index = FindBlendShape(mesh, b.blendShapeName);
            if (b.index < 0)
            {
                Debug.LogWarning($"BlendShapeKeyController: blend shape '{b.blendShapeName}' not found on {target.name}.");
                continue;
            }

            string key = (b.shift ? "Shift+" : "") + b.key.ToString().Replace("Alpha", "");
            keyLabels[b.index] = keyLabels.TryGetValue(b.index, out string existing) ? existing + ", " + key : key;
        }
    }

    /// <summary>True while a text field in the UI has focus, so shortcuts should ignore keys.</summary>
    public static bool TypingInUI => GUIUtility.keyboardControl != 0;

    void Update()
    {
        if (TypingInUI)
            return;

        bool shiftHeld = Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift);

        foreach (Binding b in bindings)
        {
            if (b.index < 0)
                continue;

            bool bankActive = b.shift == shiftHeld;
            if (toggleMode)
            {
                if (bankActive && Pressed(b))
                    SetExpression(b.index, targetWeights[b.index] <= 0f);
            }
            else
            {
                // Held keys combine, so exclusive mode doesn't apply here.
                bool held = bankActive && (Input.GetKey(b.key) || (b.altKey != KeyCode.None && Input.GetKey(b.altKey)));
                if (held)
                    targetWeights[b.index] = onWeight;
                else if (b.index != browseIndex)
                    targetWeights[b.index] = 0f;
            }
        }

        if (Input.GetKeyDown(browseNextKey))
            Browse(1);
        if (Input.GetKeyDown(browsePrevKey))
            Browse(-1);
        if (Input.GetKeyDown(resetKey))
            ResetAll();
    }

    // Weights are written in LateUpdate so they win over any blend shape
    // curves in the body animation, which the Animator applies after Update.
    void LateUpdate()
    {
        float step = transitionSpeed * Time.deltaTime;
        for (int i = 0; i < targetWeights.Length; i++)
        {
            currentWeights[i] = Mathf.MoveTowards(currentWeights[i], targetWeights[i], step);
            target.SetBlendShapeWeight(i, currentWeights[i]);
        }
    }

    /// <summary>Turns a blend shape on or off by index, honoring exclusive mode.</summary>
    public void SetExpression(int index, bool on)
    {
        if (on && exclusive)
            ClearTargets();
        targetWeights[index] = on ? onWeight : 0f;
    }

    /// <summary>Turns a blend shape on or off by name. Returns false if the name isn't on the mesh.</summary>
    public bool SetExpression(string blendShapeName, bool on)
    {
        int index = FindBlendShape(target.sharedMesh, blendShapeName);
        if (index < 0)
            return false;
        SetExpression(index, on);
        return true;
    }

    /// <summary>Sets a raw target weight (0-100) without touching other shapes.</summary>
    public void SetWeight(int index, float weight)
    {
        targetWeights[index] = Mathf.Clamp(weight, 0f, 100f);
    }

    public void ResetAll()
    {
        ClearTargets();
        browseIndex = -1;
    }

    void Browse(int direction)
    {
        int count = targetWeights.Length;
        browseIndex = browseIndex < 0
            ? (direction > 0 ? 0 : count - 1)
            : (browseIndex + direction + count) % count;
        ClearTargets();
        targetWeights[browseIndex] = onWeight;
    }

    void ClearTargets()
    {
        for (int i = 0; i < targetWeights.Length; i++)
            targetWeights[i] = 0f;
    }

    static bool Pressed(Binding b)
    {
        return Input.GetKeyDown(b.key) || (b.altKey != KeyCode.None && Input.GetKeyDown(b.altKey));
    }

    // Exact name first, then a suffix match, since some exporters prefix
    // shape names with the mesh or deformer name ("Face.blink").
    static int FindBlendShape(Mesh mesh, string name)
    {
        if (string.IsNullOrEmpty(name))
            return -1;

        int index = mesh.GetBlendShapeIndex(name);
        if (index >= 0)
            return index;

        for (int i = 0; i < mesh.blendShapeCount; i++)
        {
            string candidate = mesh.GetBlendShapeName(i);
            if (candidate.EndsWith("." + name, StringComparison.Ordinal))
                return i;
        }
        return -1;
    }

    SkinnedMeshRenderer FindFaceRenderer()
    {
        SkinnedMeshRenderer best = null;
        int bestCount = 0;

        SkinnedMeshRenderer[] candidates = GetComponentsInChildren<SkinnedMeshRenderer>();
        if (candidates.Length == 0)
            candidates = FindObjectsOfType<SkinnedMeshRenderer>();

        foreach (SkinnedMeshRenderer smr in candidates)
        {
            int count = smr.sharedMesh != null ? smr.sharedMesh.blendShapeCount : 0;
            if (count > bestCount)
            {
                best = smr;
                bestCount = count;
            }
        }
        return best;
    }

    void OnGUI()
    {
        if (!showOverlay || target == null || shapeNames == null)
            return;

        if (rowStyle == null)
        {
            rowStyle = new GUIStyle(GUI.skin.button) { alignment = TextAnchor.MiddleLeft, fontSize = 13 };
            activeRowStyle = new GUIStyle(rowStyle) { fontStyle = FontStyle.Bold };
            activeRowStyle.normal.textColor = activeRowStyle.hover.textColor = new Color(1f, 0.85f, 0.3f);
        }

        float width = 280f;
        float height = panelExpanded ? Mathf.Min(Screen.height - 20f, 120f + shapeNames.Length * 24f) : 34f;
        var panel = new Rect(10, 10, width, height);

        // Clicking anywhere outside the panel leaves the filter field, so
        // keyboard shortcuts work again.
        if (Event.current.type == EventType.MouseDown && !panel.Contains(Event.current.mousePosition))
            GUIUtility.keyboardControl = 0;

        GUILayout.BeginArea(panel, GUI.skin.box);

        GUILayout.BeginHorizontal();
        if (GUILayout.Button($"{(panelExpanded ? "\u25BC" : "\u25BA")} Shape keys ({shapeNames.Length})", GUILayout.ExpandWidth(true)))
            panelExpanded = !panelExpanded;
        if (GUILayout.Button("Reset", GUILayout.Width(55)))
            ResetAll();
        GUILayout.EndHorizontal();

        if (panelExpanded)
        {
            GUILayout.BeginHorizontal();
            GUILayout.Label("Filter", GUILayout.Width(36));
            filter = GUILayout.TextField(filter ?? "");
            GUILayout.EndHorizontal();
            exclusive = GUILayout.Toggle(exclusive, " One at a time");

            scroll = GUILayout.BeginScrollView(scroll);
            for (int i = 0; i < shapeNames.Length; i++)
            {
                if (!string.IsNullOrEmpty(filter) && shapeNames[i].IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;

                bool on = targetWeights[i] > 0f;
                string label = keyLabels.TryGetValue(i, out string keys) ? $"{shapeNames[i]}   [{keys}]" : shapeNames[i];
                if (GUILayout.Button(label, on ? activeRowStyle : rowStyle))
                    SetExpression(i, !on);
            }
            GUILayout.EndScrollView();
        }

        GUILayout.EndArea();
    }
}
