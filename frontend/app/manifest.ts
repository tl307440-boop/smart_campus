import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "智悦校园 · 情绪与学习伴侣",
    short_name: "智悦伴侣",
    description: "知情共情校园 AI 伴侣：压力微待办拆解、情绪树洞轨迹与白噪音减压舱。",
    start_url: "/",
    display: "standalone",
    background_color: "#f5ece2",
    theme_color: "#fffaf4",
    icons: [
      {
        src: "/pet/girl.webp",
        sizes: "512x512",
        type: "image/webp",
      },
    ],
  };
}
