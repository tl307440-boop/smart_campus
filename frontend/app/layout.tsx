import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { AuthGuard } from "@/components/AuthGuard";
import { AuthProvider } from "@/lib/auth/context";
import "./globals.css";

// 霞鹜漫黑（OFL，见 app/fonts/LXGWMarkerGothic-OFL.txt），子集 = 界面用字 + GB2312 一级字
const marker = localFont({
  src: "./fonts/LXGWMarkerGothic-subset.woff2",
  variable: "--font-marker",
  display: "swap",
});

export const metadata: Metadata = {
  title: "智悦校园",
  description: "校园问答助手：问规章、查资助、解疑惑。",
};

export const viewport: Viewport = {
  themeColor: "#fffaf4",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className={`${marker.variable} h-full antialiased`}>
      <body className="min-h-full">
        <svg width="0" height="0" className="absolute" aria-hidden="true" focusable="false">
          <filter id="ink-wobble" x="-12%" y="-12%" width="124%" height="124%">
            <feTurbulence type="fractalNoise" baseFrequency="0.04" numOctaves="2" seed="7" result="noise" />
            <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.6" />
          </filter>
        </svg>
        <AuthProvider>
          <AuthGuard>{children}</AuthGuard>
        </AuthProvider>
      </body>
    </html>
  );
}
