type Variant = "light" | "dark";

const WORDMARK_DARK_SRC = "/pwc-wordmark-dark.png";

export function PwcWordmark({
  variant = "light",
  product,
  size = "md",
  layout = "inline",
}: {
  variant?: Variant;
  product?: string;
  size?: "sm" | "md" | "lg";
  /** `stacked`: partner logo above product name (e.g. sidebar lockup). */
  layout?: "inline" | "stacked";
}) {
  const text = variant === "dark" ? "text-white" : "text-foreground";
  const sub = variant === "dark" ? "text-white/70" : "text-muted-foreground";
  const wordSize = size === "sm" ? "text-base" : size === "lg" ? "text-2xl" : "text-lg";

  const imgHeight = size === "sm" ? "h-5" : size === "lg" ? "h-10" : "h-[22px]";
  const imgMaxWInline =
    size === "sm" ? "max-w-[96px]" : size === "lg" ? "max-w-[min(100%,14rem)]" : "max-w-[132px]";
  const imgHeightStacked = size === "sm" ? "h-[22px]" : size === "lg" ? "h-10" : "h-[26px]";

  if (variant === "dark") {
    if (layout === "stacked") {
      return (
        <div className="flex w-full min-w-0 flex-col gap-2">
          <img
            src={WORDMARK_DARK_SRC}
            alt="PwC"
            decoding="async"
            className={`${imgHeightStacked} w-auto max-w-[min(100%,9rem)] shrink-0 object-contain object-left`}
          />
          {product ? (
            <span className="font-semibold tracking-[-0.02em] text-[17px] leading-none text-sidebar-foreground">
              {product}
            </span>
          ) : null}
        </div>
      );
    }

    return (
      <div className="flex min-w-0 items-center gap-3">
        <img
          src={WORDMARK_DARK_SRC}
          alt="PwC"
          decoding="async"
          className={`${imgHeight} w-auto ${imgMaxWInline} shrink-0 object-contain object-left`}
        />
        {product ? (
          <>
            <span className="h-5 w-px shrink-0 bg-sidebar-foreground/18" aria-hidden />
            <span
              className={`${wordSize} min-w-0 truncate font-semibold tracking-[-0.03em] leading-none text-sidebar-foreground`}
            >
              {product}
            </span>
          </>
        ) : null}
      </div>
    );
  }

  const bar =
    size === "sm"
      ? {
          first: "h-[5px] w-[11px]",
          second: "h-[6px] w-[11px]",
          gap: "gap-px",
          lift: "-translate-y-px",
        }
      : size === "lg"
        ? {
            first: "h-[8px] w-[17px]",
            second: "h-[10px] w-[17px]",
            gap: "gap-0.5",
            lift: "-translate-y-1",
          }
        : {
            first: "h-[6px] w-[13px]",
            second: "h-[8px] w-[13px]",
            gap: "gap-0.5",
            lift: "-translate-y-0.5",
          };

  return (
    <div className="flex items-center gap-2.5">
      <div className={`flex items-end ${bar.gap}`} aria-hidden>
        <span className={`${bar.first} shrink-0 bg-[#EB8C00] -skew-x-[22deg]`} />
        <span className={`${bar.second} shrink-0 bg-[#EB8C00] -skew-x-[22deg] ${bar.lift}`} />
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`${wordSize} font-serif font-bold tracking-tight leading-none ${text}`}>pwc</span>
        {product ? (
          <span className={`${wordSize} font-medium tracking-tight leading-none ${sub}`}>{product}</span>
        ) : null}
      </div>
    </div>
  );
}
