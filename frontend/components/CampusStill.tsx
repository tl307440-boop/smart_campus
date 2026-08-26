"use client";

import { useState } from "react";
import Image from "next/image";
import { SCHOOL_MARK, type CampusScene } from "@/lib/campus/scenes";

type CampusStillProps = {
  readonly scene: CampusScene;
  readonly sizes: string;
  readonly priority?: boolean;
  readonly className?: string;
  readonly imageClassName?: string;
  readonly decorative?: boolean;
};

/**
 * 本地 public/campus 风光，用 next/image 的 fill + sizes。
 * 加载失败时退回 CSS 氛围底，避免热链官网。
 */
export function CampusStill({
  scene,
  sizes,
  priority = false,
  className,
  imageClassName,
  decorative = false,
}: CampusStillProps) {
  const [failed, setFailed] = useState(false);
  const alt = decorative ? "" : `${SCHOOL_MARK.name} ${scene.title}`;

  return (
    <div className={`relative h-full w-full overflow-hidden ${className ?? ""}`}>
      {failed ? (
        <div className="campus-atmosphere absolute inset-0" aria-hidden />
      ) : (
        <Image
          src={scene.src}
          alt={alt}
          fill
          sizes={sizes}
          priority={priority}
          className={`object-cover ${imageClassName ?? ""}`}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}
