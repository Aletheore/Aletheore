# Brand Voice to Visual Style Mapping

> Updated: 2026-04-01
> Source: Used by creative-strategist and visual-designer agents

## Purpose

Brand voice scores from `brand-profile.json` can suggest visual hypotheses; they do
not translate deterministically into a visual identity. Brand examples, approved
assets, accessibility, culture, audience, placement, and the creative brief take
precedence. Use mappings below to propose testable directions and require human
review before generation or publication.

## Axis Mappings

### 1. formal_casual

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Clean lines, symmetrical, muted palette, serif type     |
| Mid       | 4-6   | Balanced layout, neutral tones, modern sans-serif       |
| High      | 7-10  | Organic shapes, warm palette, hand-drawn elements       |

### 2. rational_emotional

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Data overlays, charts, structured grids, cool tones     |
| Mid       | 4-6   | Infographic style, balanced data and imagery            |
| High      | 7-10  | Expressive color, human faces, dynamic motion blur      |

### 3. bold_subtle

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Soft focus, pastel tones, whitespace, thin strokes      |
| Mid       | 4-6   | Medium contrast, standard weight type, clear hierarchy  |
| High      | 7-10  | High contrast, saturated color, heavy type, full bleed  |

### 4. traditional_innovative

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Classic compositions, heritage textures, earth tones    |
| Mid       | 4-6   | Contemporary layouts, balanced modern and classic cues  |
| High      | 7-10  | Futuristic gradients, 3D renders, neon accents, glass   |

### 5. expert_accessible

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Technical diagrams, denser information, specialist cues |
| Mid       | 4-6   | Balanced explanation, clear hierarchy, mixed detail     |
| High      | 7-10  | Plain-language labels, generous hierarchy, familiar cues |

### 6. playful_serious

| Range     | Score | Visual Descriptors                                      |
|-----------|-------|---------------------------------------------------------|
| Low       | 1-3   | Whimsical patterns, cartoon elements, candy colors      |
| Mid       | 4-6   | Lifestyle photography, natural color grading            |
| High      | 7-10  | Cinematic lighting, desaturated palette, minimal decor  |

## How to Apply

1. Read `brand-profile.json` and extract the six voice axis scores.
2. For each axis, treat the matching range as candidate descriptors only.
3. Compare candidates with supplied brand assets and remove contradictions,
   stereotypes, inaccessible choices, and culturally unsafe assumptions.
4. Draft at least two materially different style directions when evidence is weak.
5. Record the selected direction and owner approval in the creative manifest, then
   pass normalized descriptors to the installed image capability.

## Example

Given a brand with these scores:

- formal_casual: 7 (High; organic shapes, warm palette)
- bold_subtle: 8 (High; high contrast, saturated color)
- playful_serious: 3 (Low; whimsical patterns, candy colors)

The resulting [STYLE] component:

```
[STYLE]: organic shapes, warm palette, high contrast, saturated color,
whimsical patterns, candy colors
```

This is one hypothesis for review. It is not evidence that the brand has this
identity or that the direction will improve performance.
