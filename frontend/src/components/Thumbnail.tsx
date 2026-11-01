/**
 * The thumbnail of a stored photo, shown through its file link.
 */
import { useQuery } from "@tanstack/react-query";
import type { Api } from "../api";
import { fileQuery } from "../fileQuery";
import { texts } from "../texts";

export function Thumbnail({ imageKey, api, onOpen }: {
  imageKey: string;
  api: Api;
  onOpen: (src: string) => void;
}) {
  const url = useQuery(fileQuery(api, imageKey));
  if (url.isError) return <span className="muted">{texts.photoUnavailable}</span>;
  if (url.data === undefined) return null;
  return (
    <button type="button" className="shot" onClick={() => onOpen(url.data)}>
      <img src={url.data} alt={texts.photo} />
    </button>
  );
}
