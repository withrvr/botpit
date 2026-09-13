import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const wordmark = (
    <span className={cn("text-xl font-bold tracking-tight", className)}>
      botpit
    </span>
  )
  const monogram = (
    <span
      className={cn(
        "flex size-5 items-center justify-center rounded-sm bg-foreground text-xs font-bold text-background",
        className,
      )}
    >
      b
    </span>
  )

  const content =
    variant === "responsive" ? (
      <>
        <span className="group-data-[collapsible=icon]:hidden">{wordmark}</span>
        <span className="hidden group-data-[collapsible=icon]:flex">
          {monogram}
        </span>
      </>
    ) : variant === "icon" ? (
      monogram
    ) : (
      wordmark
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
