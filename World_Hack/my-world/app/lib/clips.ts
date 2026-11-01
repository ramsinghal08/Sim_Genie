export interface PresetClip {
  id: string;
  label: string;
  src: string;
}

// Demo source videos, each paired with a demo prompt: ball (swap object),
// dog (replace with reference), figure (follow the drag).
export const PRESET_CLIPS: readonly PresetClip[] = [
  {
    id: "ball",
    label: "Red ball (swap object)",
    src: "/clips/ball.mp4",
  },
  {
    id: "dog",
    label: "Dog (replace with reference)",
    src: "/clips/dog.mp4",
  },
  {
    id: "figure",
    label: "Figure (drag to animate)",
    src: "/clips/figure.mp4",
  },
] as const;

export interface PresetImage {
  id: string;
  label: string;
  src: string;
}

// Demo images, used as image-mode sources and reference presets. All 16:9
// landscape to match the clips and the model's output (portrait references
// distort).
export const PRESET_IMAGES: readonly PresetImage[] = [
  { id: "kitten", label: "Kitten", src: "/refs/kitten.jpg" },
  { id: "knight", label: "Knight", src: "/refs/knight.jpg" },
  { id: "wizard", label: "Wizard", src: "/refs/wizard.jpg" },
] as const;
