/** 附图：在浏览器里先缩小再发，手机拍的几 MB 原图直接发既慢又会被后端拒掉 */

export const MAX_IMAGES = 2;
/** 长边上限；识图模型一般自己也会缩，再大只是白费流量 */
const MAX_EDGE = 1280;
const JPEG_QUALITY = 0.82;
/** 选的原图超过这个大小就不读了，免得手机内存吃紧 */
const MAX_SOURCE_BYTES = 25 * 1024 * 1024;

export class ImageAttachError extends Error {}

function load(file: File): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new ImageAttachError("这张图打不开，换一张试试（支持 JPG、PNG、WebP）"));
    };
    img.src = url;
  });
}

/** 缩到长边 ≤1280 并转成 JPEG 的 data URL；透明底铺白，免得 PNG 截图变黑 */
export async function prepareImage(file: File): Promise<string> {
  if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) {
    throw new ImageAttachError("只能附 JPG、PNG、WebP 图片");
  }
  if (file.size > MAX_SOURCE_BYTES) throw new ImageAttachError("图片太大了，请换一张小一点的");

  const img = await load(file);
  const scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
  const width = Math.max(1, Math.round(img.naturalWidth * scale));
  const height = Math.max(1, Math.round(img.naturalHeight * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new ImageAttachError("这个浏览器处理不了图片");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, width, height);
  context.drawImage(img, 0, 0, width, height);
  return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
}
