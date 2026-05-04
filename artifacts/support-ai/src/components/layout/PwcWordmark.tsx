type Variant = "light" | "dark";

export function PwcWordmark({
  variant = "light",
  product,
  size = "md",
}: {
  variant?: Variant;
  product?: string;
  size?: "sm" | "md" | "lg";
}) {
  const text = variant === "dark" ? "text-white" : "text-foreground";
  const sub = variant === "dark" ? "text-white/70" : "text-muted-foreground";
  const dot = size === "sm" ? "h-1.5 w-1.5" : size === "lg" ? "h-2.5 w-2.5" : "h-2 w-2";
  const wordSize = size === "sm" ? "text-base" : size === "lg" ? "text-2xl" : "text-lg";

  return (
    <div className="flex items-center gap-2.5">
      <div className="flex flex-col gap-[2px]">
        <div className="flex gap-[2px]">
          <span className={`${dot} bg-[#FFB600] block`} />
          <span className={`${dot} bg-[#EB8C00] block`} />
          <span className={`${dot} bg-[#D04A02] block`} />
          <span className={`${dot} bg-[#E0301E] block`} />
        </div>
        <div className="flex gap-[2px] opacity-0 h-0">
          <span className={dot} />
        </div>
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`${wordSize} font-black tracking-tight leading-none ${text}`}>pwc</span>
        {product ? (
          <span className={`${wordSize} font-medium tracking-tight leading-none ${sub}`}>{product}</span>
        ) : null}
      </div>
    </div>
  );
}
