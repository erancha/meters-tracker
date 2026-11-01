import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downscale } from "./images";

const drawImage = vi.fn();

function stubCanvas(width: number, height: number) {
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width, height, close: vi.fn() }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (this: HTMLCanvasElement, cb, type, q) {
    toBlobArgs = [type, q, this.width, this.height];
    cb(new Blob(["jpegbytes"], { type: "image/jpeg" }));
  });
}

let toBlobArgs: unknown[] = [];

describe("downscale", () => {
  beforeEach(() => drawImage.mockClear());
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("keeps a small image's size", async () => {
    stubCanvas(800, 600);

    const out = await downscale(new File(["x"], "a.png", { type: "image/png" }));

    expect(toBlobArgs).toEqual(["image/jpeg", 0.85, 800, 600]);
    expect(out).toEqual({ base64: btoa("jpegbytes"), mediaType: "image/jpeg" });
  });

  it("shrinks a large image so its longer edge is 1600", async () => {
    stubCanvas(4000, 3000);

    await downscale(new File(["x"], "a.jpg", { type: "image/jpeg" }));

    expect(toBlobArgs).toEqual(["image/jpeg", 0.85, 1600, 1200]);
  });

  it("shrinks a tall image by its height", async () => {
    stubCanvas(3000, 6000);

    await downscale(new File(["x"], "a.jpg", { type: "image/jpeg" }));

    expect(toBlobArgs).toEqual(["image/jpeg", 0.85, 800, 1600]);
  });
});

describe("downscale encoding", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("fails loudly when the browser cannot encode JPEG", async () => {
    stubCanvas(10, 10);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation((cb) => cb(new Blob(["p"], { type: "image/png" })));

    await expect(downscale(new File(["x"], "a.jpg"))).rejects.toThrow("image/png");
  });
});
