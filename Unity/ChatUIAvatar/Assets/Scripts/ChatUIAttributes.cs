using UnityEngine;

/// <summary>
/// Shows a string field as a dropdown of the animation clips in
/// Assets/Resources/Animations (file names) in the Inspector.
/// </summary>
public class AnimationNameAttribute : PropertyAttribute
{
}

/// <summary>
/// Shows a string field as a dropdown of the shape keys on the avatar's face
/// mesh in the Inspector.
/// </summary>
public class ShapeKeyNameAttribute : PropertyAttribute
{
}
