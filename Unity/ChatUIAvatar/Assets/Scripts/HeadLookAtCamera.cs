using UnityEngine;

/// <summary>
/// Turns the avatar's neck and head toward the camera on top of whatever
/// body animation is playing. Runs in LateUpdate, after the Animator has
/// posed the skeleton, and splits the turn between the neck and head bones
/// so it doesn't look like the head is on a swivel.
///
/// Facing is taken from the avatar root's forward axis in the starting pose,
/// so it doesn't depend on how the bones' own axes were exported.
/// L toggles it on and off.
/// </summary>
public class HeadLookAtCamera : MonoBehaviour
{
    [Tooltip("What to look at. If empty, the main camera.")]
    public Transform lookTarget;

    [Tooltip("Avatar root. If empty, the root of the face mesh found by the BlendShapeKeyController is used.")]
    public Transform avatarRoot;

    public string neckBoneName = "Neck";
    public string headBoneName = "Head";

    [Tooltip("Flip if the head turns away from the camera instead of toward it.")]
    public bool characterFacesNegativeZ;

    [Range(0f, 1f)] public float weight = 1f;

    [Tooltip("Share of the turn done by the neck; the head does the rest.")]
    [Range(0f, 1f)] public float neckShare = 0.4f;

    [Tooltip("Largest total turn in degrees. Targets further round than this are followed only up to the limit.")]
    [Range(0f, 90f)] public float maxAngle = 55f;

    [Tooltip("How quickly the head catches up with the target. 0 snaps.")]
    public float smoothing = 6f;

    public KeyCode toggleKey = KeyCode.L;

    Transform neck;
    Transform head;
    Vector3 headForwardLocal;
    Quaternion smoothedTurn = Quaternion.identity;
    float activeWeight;
    bool active = true;

    void Start()
    {
        if (lookTarget == null && Camera.main != null)
            lookTarget = Camera.main.transform;

        if (avatarRoot == null)
        {
            var face = FindObjectOfType<BlendShapeKeyController>();
            if (face != null && face.target != null)
                avatarRoot = face.target.transform.root;
        }
        if (avatarRoot == null)
        {
            enabled = false;
            return;
        }

        neck = FindBone(avatarRoot, neckBoneName);
        head = FindBone(avatarRoot, headBoneName);
        if (head == null)
        {
            Debug.LogWarning($"HeadLookAtCamera: bone '{headBoneName}' not found.");
            enabled = false;
            return;
        }

        // Start runs before the first animation frame, so this is the rest pose.
        Vector3 forward = characterFacesNegativeZ ? -avatarRoot.forward : avatarRoot.forward;
        headForwardLocal = Quaternion.Inverse(head.rotation) * forward;
        activeWeight = weight;
    }

    void Update()
    {
        if (Input.GetKeyDown(toggleKey))
            active = !active;
    }

    void LateUpdate()
    {
        if (lookTarget == null)
            return;

        float t = smoothing <= 0f ? 1f : 1f - Mathf.Exp(-smoothing * Time.deltaTime);
        activeWeight = Mathf.Lerp(activeWeight, active ? weight : 0f, t);

        Vector3 headForward = head.rotation * headForwardLocal;
        Vector3 toTarget = lookTarget.position - head.position;
        Quaternion turn = Quaternion.FromToRotation(headForward, toTarget);
        turn = Quaternion.RotateTowards(Quaternion.identity, turn, maxAngle);
        smoothedTurn = Quaternion.Slerp(smoothedTurn, turn, t);

        Quaternion applied = Quaternion.Slerp(Quaternion.identity, smoothedTurn, activeWeight);
        if (neck != null && neck != head)
        {
            Quaternion neckTurn = Quaternion.Slerp(Quaternion.identity, applied, neckShare);
            neck.rotation = neckTurn * neck.rotation;
            // The head inherited the neck's turn; give it the remainder.
            applied = applied * Quaternion.Inverse(neckTurn);
        }
        head.rotation = applied * head.rotation;
    }

    static Transform FindBone(Transform root, string boneName)
    {
        foreach (Transform t in root.GetComponentsInChildren<Transform>())
        {
            if (t.name == boneName)
                return t;
        }
        return null;
    }
}
