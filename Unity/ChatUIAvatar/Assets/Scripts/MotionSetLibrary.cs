using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Named motion sets: a body animation, its playback speed and a group of
/// shape keys, fused into one thing that can be played by name, from the
/// on-screen panel or (later) by the LLM through AvatarApiServer.
///
/// Edit the list in the Inspector on AvatarController. The animation and
/// shape key fields are dropdowns filled from the animation folder and the
/// face mesh. Speed lives here rather than on the animation, so the same clip
/// can play at different speeds in different sets.
///
/// A set lasts until another one is played, or until its Duration runs out,
/// or (when its animation plays once and Duration is 0) until the animation
/// finishes; then the default set plays again.
/// </summary>
public class MotionSetLibrary : MonoBehaviour
{
    public enum LoopMode { AnimationDefault, Loop, Once }

    [Serializable]
    public class ShapeWeight
    {
        [ShapeKeyName] public string shapeKey;
        [Range(0f, 100f)] public float weight = 100f;

        public ShapeWeight() { }

        public ShapeWeight(string shapeKey, float weight = 100f)
        {
            this.shapeKey = shapeKey;
            this.weight = weight;
        }
    }

    [Serializable]
    public class MotionSet
    {
        [Tooltip("Name used by the panel and the API, e.g. happy. Keep it short and unique.")]
        public string name;

        [Tooltip("What the set looks like / when to use it. Sent to the LLM with the list of sets.")]
        [TextArea(1, 3)] public string description;

        [Tooltip("Body animation. Empty keeps whatever is playing.")]
        [AnimationName] public string animation;

        [Tooltip("After a play-once Animation ends, carry on looping this one, e.g. sit down then sitting. The set then lasts until another plays.")]
        [AnimationName] public string thenLoop;

        [Tooltip("Playback speed for this set: 1 is normal, 0.5 half speed, 2 double.")]
        [Min(0.01f)] public float speed = 1f;

        [Tooltip("Animation Default uses the clip's own loop setting from the animation list.")]
        public LoopMode loop = LoopMode.AnimationDefault;

        [Tooltip("Seconds before going back to the default set. 0 stays until another set plays (or until a play-once animation ends).")]
        [Min(0f)] public float duration;

        public List<ShapeWeight> shapeKeys = new List<ShapeWeight>();

        public MotionSet() { }

        public MotionSet Then(string loopAnimation)
        {
            thenLoop = loopAnimation;
            return this;
        }

        public MotionSet(string name, string description, string animation, LoopMode loop, float duration, params ShapeWeight[] shapeKeys)
        {
            this.name = name;
            this.description = description;
            this.animation = animation;
            this.loop = loop;
            this.duration = duration;
            this.shapeKeys = new List<ShapeWeight>(shapeKeys);
        }
    }

    [Tooltip("Set played at start and whenever a set ends. Empty uses the first one.")]
    public string defaultMotionSet = "idle";

    public bool showOverlay = true;

    public List<MotionSet> motionSets = new List<MotionSet>
    {
        new MotionSet("idle", "Relaxed, neutral standing.", "idle breathe", LoopMode.Loop, 0f),
        new MotionSet("happy", "Pleased or excited about something.", "Excited", LoopMode.Once, 0f,
            new ShapeWeight("happy")),
        new MotionSet("joyful", "Very happy, celebrating.", "Joyful Jump", LoopMode.Once, 0f,
            new ShapeWeight("joy")),
        new MotionSet("angry", "Annoyed or angry.", "Angry", LoopMode.Once, 0f,
            new ShapeWeight("angry")),
        new MotionSet("sad", "Sad or disappointed.", "", LoopMode.AnimationDefault, 6f,
            new ShapeWeight("sad")),
        new MotionSet("surprised", "Caught off guard, surprised.", "", LoopMode.AnimationDefault, 3f,
            new ShapeWeight("surprised")),
        new MotionSet("smug", "Teasing, smug, confident.", "", LoopMode.AnimationDefault, 5f,
            new ShapeWeight("smug")),
        new MotionSet("embarrassed", "Flustered or shy.", "", LoopMode.AnimationDefault, 5f,
            new ShapeWeight("embarrassed")),
        new MotionSet("excited", "Thrilled, can't wait.", "Excited", LoopMode.Once, 0f,
            new ShapeWeight("excited")),
        new MotionSet("laugh", "Laughing out loud at something funny.", "", LoopMode.AnimationDefault, 4f,
            new ShapeWeight("laugh")),
        new MotionSet("giggle", "A small, cute giggle.", "", LoopMode.AnimationDefault, 3f,
            new ShapeWeight("giggle")),
        new MotionSet("beaming", "Big proud smile.", "", LoopMode.AnimationDefault, 5f,
            new ShapeWeight("beaming")),
        new MotionSet("cheerful", "Bright, open-mouthed happiness.", "", LoopMode.AnimationDefault, 5f,
            new ShapeWeight("happy open")),
        new MotionSet("content", "Calm and satisfied.", "", LoopMode.AnimationDefault, 8f,
            new ShapeWeight("content")),
        new MotionSet("wink", "A cheerful wink.", "", LoopMode.AnimationDefault, 2f,
            new ShapeWeight("cheerful wink")),
        new MotionSet("playful", "Teasing in a fun, mischievous way.", "", LoopMode.AnimationDefault, 5f,
            new ShapeWeight("playful")),
        new MotionSet("adoring", "Affectionate, looking at someone she likes.", "", LoopMode.AnimationDefault, 6f,
            new ShapeWeight("adoring")),
        new MotionSet("nod", "Agreeing, yes.", "nod", LoopMode.Once, 0f),
        new MotionSet("head shake", "Disagreeing, no.", "head shake", LoopMode.Once, 0f),
        new MotionSet("head tilt", "Curious or puzzled.", "head tilt", LoopMode.Once, 0f,
            new ShapeWeight("smile soft")),
        new MotionSet("wave", "Saying hello or goodbye.", "wave", LoopMode.Once, 0f,
            new ShapeWeight("happy")),
        new MotionSet("bow", "Polite greeting or thanks.", "bow", LoopMode.Once, 0f),
        new MotionSet("shrug", "Not sure, who knows.", "shrug", LoopMode.Once, 0f),
        new MotionSet("shy", "Shy and fidgety.", "shy fidget", LoopMode.Once, 0f,
            new ShapeWeight("embarrassed")),
        new MotionSet("hmph", "Tsundere huff, looking away.", "hmph look away", LoopMode.Once, 0f,
            new ShapeWeight("tsun hmph")),
        new MotionSet("arms crossed", "Unimpressed, waiting.", "arms crossed", LoopMode.Once, 0f,
            new ShapeWeight("annoyed")),
        new MotionSet("hand on hip", "Casual and confident.", "left hand on hip", LoopMode.Once, 0f,
            new ShapeWeight("smug")),
        new MotionSet("hands on hips", "Proud or scolding.", "both hands on hips", LoopMode.Once, 0f),
        new MotionSet("lean in", "Leaning in, teasing.", "lean forward hands on hips", LoopMode.Once, 0f,
            new ShapeWeight("playful")),
        new MotionSet("sit", "Sits down on the floor and stays sitting.", "sit down on floor", LoopMode.Once, 0f).Then("sit on floor loop"),
        new MotionSet("stand up", "Stands up from sitting on the floor.", "stand up from floor", LoopMode.Once, 0f),
        new MotionSet("lie down", "Lies down on the floor and stays there.", "lay down on floor", LoopMode.Once, 0f).Then("lay on floor loop"),
        new MotionSet("sleepy", "Tired, lies down on the floor.", "lay down on floor", LoopMode.Once, 0f,
            new ShapeWeight("sleepy")).Then("lay on floor loop"),
        new MotionSet("get up", "Gets up from lying on the floor.", "get up from floor", LoopMode.Once, 0f),
    };

    /// <summary>Bottom edge of the motion set panel, so the animation panel can sit under it.</summary>
    public static float RightPanelBottom { get; private set; }

    /// <summary>Raised after a set starts playing.</summary>
    public event Action<MotionSet> Played;

    /// <summary>The set playing now, or null after a clip was picked by hand.</summary>
    public MotionSet Current { get; private set; }

    BlendShapeKeyController face;
    AvatarAnimationPlayer player;
    AvatarAnimationPlayer.Entry setEntry;
    bool startingClip;
    float endTime;
    bool panelExpanded = true;
    Vector2 scroll;
    GUIStyle rowStyle;
    GUIStyle activeRowStyle;

    void Awake()
    {
        RightPanelBottom = 0f;
    }

    void OnDestroy()
    {
        RightPanelBottom = 0f;
    }

    IEnumerator Start()
    {
        face = FindObjectOfType<BlendShapeKeyController>();
        player = FindObjectOfType<AvatarAnimationPlayer>();
        if (player != null)
        {
            player.Finished += OnClipFinished;
            player.Started += OnClipStarted;
        }

        // Let the animation player load its clips first.
        yield return null;
        PlayDefault();
    }

    void Update()
    {
        if (Current != null && endTime > 0f && Time.time >= endTime)
            PlayDefault();
    }

    /// <summary>Plays a set by name (ignoring case). Returns false if there is no such set.</summary>
    public bool Play(string setName)
    {
        MotionSet set = Find(setName);
        if (set == null)
            return false;
        Play(set);
        return true;
    }

    public void Play(MotionSet set)
    {
        Current = set;
        endTime = set.duration > 0f ? Time.time + set.duration : 0f;

        if (face != null)
        {
            face.ClearLayer(BlendShapeKeyController.Layer.MotionSet);
            foreach (ShapeWeight s in set.shapeKeys)
            {
                int index = face.FindShape(s.shapeKey);
                if (index >= 0)
                    face.SetLayerWeight(BlendShapeKeyController.Layer.MotionSet, index, s.weight);
                else if (!string.IsNullOrEmpty(s.shapeKey))
                    Debug.LogWarning($"MotionSetLibrary: '{set.name}' uses shape key '{s.shapeKey}', which isn't on the face mesh.");
            }
        }

        setEntry = null;
        if (player != null && !string.IsNullOrEmpty(set.animation))
        {
            AvatarAnimationPlayer.Entry entry = player.Find(set.animation);
            if (entry?.clip == null)
            {
                Debug.LogWarning($"MotionSetLibrary: '{set.name}' uses animation '{set.animation}', which wasn't found.");
            }
            else
            {
                bool loop = set.loop == LoopMode.AnimationDefault ? entry.loop : set.loop == LoopMode.Loop;
                setEntry = entry;
                startingClip = true;
                player.Play(entry, set.speed, loop);
                startingClip = false;
            }
        }

        Played?.Invoke(set);
    }

    public void PlayDefault()
    {
        MotionSet set = Find(defaultMotionSet);
        if (set == null && motionSets.Count > 0)
            set = motionSets[0];
        if (set != null)
            Play(set);
    }

    public MotionSet Find(string setName)
    {
        return motionSets.Find(s => string.Equals(s.name, setName, StringComparison.OrdinalIgnoreCase));
    }

    // A play-once animation that belongs to the current set either chains
    // into the set's Then Loop animation or ends the set (unless the set has
    // its own duration).
    void OnClipFinished(AvatarAnimationPlayer.Entry entry)
    {
        if (Current == null || entry != setEntry)
            return;

        AvatarAnimationPlayer.Entry next = string.IsNullOrEmpty(Current.thenLoop) ? null : player.Find(Current.thenLoop);
        if (next?.clip != null)
        {
            setEntry = next;
            startingClip = true;
            player.Play(next, Current.speed, true);
            startingClip = false;
        }
        else if (Current.duration <= 0f)
        {
            PlayDefault();
        }
    }

    // A clip picked by hand (panel or F-keys) takes over from the set's
    // animation; the set's shape keys stay until another set plays.
    void OnClipStarted(AvatarAnimationPlayer.Entry entry)
    {
        if (!startingClip && entry != setEntry)
        {
            setEntry = null;
            Current = null;
            endTime = 0f;
        }
    }

    void OnGUI()
    {
        if (!showOverlay || !BlendShapeKeyController.PanelsVisible)
        {
            RightPanelBottom = 0f;
            return;
        }

        if (rowStyle == null)
        {
            rowStyle = new GUIStyle(GUI.skin.button) { alignment = TextAnchor.MiddleLeft, fontSize = 13 };
            activeRowStyle = new GUIStyle(rowStyle) { fontStyle = FontStyle.Bold };
            activeRowStyle.normal.textColor = activeRowStyle.hover.textColor = new Color(1f, 0.85f, 0.3f);
        }

        float width = 260f;
        float height = panelExpanded ? Mathf.Min(Screen.height * 0.45f, 50f + motionSets.Count * 24f) : 34f;
        var panel = new Rect(Screen.width - width - 10, 10, width, height);
        RightPanelBottom = panel.yMax;

        BlendShapeKeyController.DrawPanelBackground(panel);
        GUILayout.BeginArea(panel, GUI.skin.box);

        if (GUILayout.Button($"{(panelExpanded ? "▼" : "►")} Motion sets ({motionSets.Count})"))
            panelExpanded = !panelExpanded;

        if (panelExpanded)
        {
            scroll = GUILayout.BeginScrollView(scroll);
            foreach (MotionSet set in motionSets)
            {
                string label = set.name;
                if (set.name == defaultMotionSet)
                    label += " (default)";
                if (GUILayout.Button(label, set == Current ? activeRowStyle : rowStyle))
                    Play(set);
            }
            GUILayout.EndScrollView();
        }

        GUILayout.EndArea();
    }
}
