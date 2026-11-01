/**
 * A card block of the chat: title, lines, and the options. With a choose handler the options are
 * buttons (confirm is the primary button, the others are chips). Once answered, the chosen
 * option shows in place: a done mark for confirm, the marked chip for the others.
 */
import type { Card, CardOption } from "../types";

export function CardView({ card, onChoose, answer = null, disabled = false }: {
  card: Card;
  onChoose?: (option: CardOption) => void;
  answer?: string | null;
  disabled?: boolean;
}) {
  const confirm = card.options.find((o) => o.id === "confirm");
  const others = card.options.filter((o) => o.id !== "confirm");
  const answered = answer !== null;
  const shown = onChoose !== undefined || answered;
  return (
    <>
      <div className="card-title">{card.title}</div>
      {card.lines.map((line, i) => <p key={i} className="text">{line}</p>)}
      {shown && others.length > 0 && (
        <div className="chips">
          {others.map((o) => (
            <button key={o.id} type="button" disabled={disabled || answered}
                    className={o.label === answer ? "chip on" : "chip"}
                    onClick={() => onChoose?.(o)}>{o.label}</button>
          ))}
        </div>
      )}
      {shown && confirm !== undefined && (!answered || answer === confirm.label) && (
        <div className="actions">
          {answered
            ? <span className="done">✓ {confirm.label}</span>
            : <button type="button" className="primary" disabled={disabled}
                      onClick={() => onChoose?.(confirm)}>{confirm.label}</button>}
        </div>
      )}
    </>
  );
}
