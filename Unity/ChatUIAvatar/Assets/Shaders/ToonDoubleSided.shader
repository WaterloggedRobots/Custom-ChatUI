// Anime-style shading for the avatar, drawn on both sides of every face.
//
// Starts from the flat palette color and adds:
// - saturation, brightness and contrast controls to make colors pop
// - a soft two-tone cel shadow from the scene's directional light, tinted
//   (cool purple by default) rather than just darker
// - a rim light along the silhouette
// Unlit-style otherwise: no shadows cast or received, so it stays clean.
Shader "ChatUI/Toon Double Sided"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Tint", Color) = (1, 1, 1, 1)
        _Saturation ("Saturation", Range(0, 2)) = 1.2
        _Brightness ("Brightness", Range(0.5, 1.5)) = 1.05
        _Contrast ("Contrast", Range(0.5, 1.5)) = 1.05
        _ShadowColor ("Shadow Tint", Color) = (0.78, 0.72, 0.9, 1)
        _ShadowThreshold ("Shadow Threshold", Range(-1, 1)) = 0.1
        _ShadowSoftness ("Shadow Softness", Range(0.001, 0.5)) = 0.06
        _RimColor ("Rim Color", Color) = (1, 0.95, 0.9, 1)
        _RimStrength ("Rim Strength", Range(0, 1)) = 0.25
        _RimPower ("Rim Width", Range(0.5, 8)) = 3
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" }
        LOD 200
        Cull Off

        Pass
        {
            Tags { "LightMode" = "ForwardBase" }

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.0
            #pragma multi_compile_fog
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
                float2 uv : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float3 worldNormal : TEXCOORD1;
                float3 worldPos : TEXCOORD2;
                UNITY_FOG_COORDS(3)
            };

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _Color;
            half _Saturation, _Brightness, _Contrast;
            fixed4 _ShadowColor;
            half _ShadowThreshold, _ShadowSoftness;
            fixed4 _RimColor;
            half _RimStrength, _RimPower;

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                o.worldNormal = UnityObjectToWorldNormal(v.normal);
                o.worldPos = mul(unity_ObjectToWorld, v.vertex).xyz;
                UNITY_TRANSFER_FOG(o, o.pos);
                return o;
            }

            fixed4 frag (v2f i, fixed facing : VFACE) : SV_Target
            {
                fixed4 tex = tex2D(_MainTex, i.uv) * _Color;
                half3 col = tex.rgb;

                // Color grading.
                half luma = dot(col, half3(0.299, 0.587, 0.114));
                col = lerp(luma.xxx, col, _Saturation);
                col = (col - 0.5) * _Contrast + 0.5;
                col *= _Brightness;

                // Back faces of the hair cards need the flipped normal.
                float3 n = normalize(i.worldNormal) * (facing > 0 ? 1 : -1);
                float3 viewDir = normalize(_WorldSpaceCameraPos - i.worldPos);
                float3 lightDir = _WorldSpaceLightPos0.w > 0
                    ? normalize(_WorldSpaceLightPos0.xyz - i.worldPos)
                    : normalize(_WorldSpaceLightPos0.xyz);
                // No light in the scene: shade as if lit from the camera.
                if (dot(_WorldSpaceLightPos0.xyz, _WorldSpaceLightPos0.xyz) < 1e-4)
                    lightDir = viewDir;

                half ndl = dot(n, lightDir);
                half lit = smoothstep(_ShadowThreshold - _ShadowSoftness, _ShadowThreshold + _ShadowSoftness, ndl);
                col *= lerp(_ShadowColor.rgb, half3(1, 1, 1), lit);

                half rim = pow(1 - saturate(dot(n, viewDir)), _RimPower) * _RimStrength;
                col += _RimColor.rgb * rim;

                fixed4 result = fixed4(saturate(col), 1);
                UNITY_APPLY_FOG(i.fogCoord, result);
                return result;
            }
            ENDCG
        }
    }
    Fallback "ChatUI/Unlit Texture Double Sided"
}
