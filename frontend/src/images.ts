// Prepares a camera photo for upload: scaled so the longer edge fits, re-encoded as JPEG.

const readBase64 = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",", 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });

const toJpeg = (canvas: HTMLCanvasElement, quality: number) =>
  new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b === null ? reject(new Error("JPEG encoding failed")) : resolve(b)),
                  "image/jpeg", quality));

/** A phone photo is several megabytes; 1600 px keeps the meter digits legible and well under 4 MB. */
export async function downscale(file: File, maxEdge = 1600, quality = 0.85):
    Promise<{ base64: string; mediaType: "image/jpeg" }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  const blob = await toJpeg(canvas, quality);
  if (blob.type !== "image/jpeg") throw new Error(`Browser encoded ${blob.type}, not JPEG`);
  return { base64: await readBase64(blob), mediaType: blob.type };
}
