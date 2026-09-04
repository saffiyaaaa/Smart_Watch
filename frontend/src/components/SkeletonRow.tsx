/** Animated shimmer skeleton for loading states. */
interface Props {
  lines?: number
  height?: number
  className?: string
  style?: React.CSSProperties
}

export default function SkeletonRow({ lines = 1, height = 16, className = '', style }: Props) {
  return (
    <div className={className} style={{ display: 'flex', flexDirection: 'column', gap: 8, ...style }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{
            height,
            width: i === lines - 1 && lines > 1 ? '65%' : '100%',
          }}
        />
      ))}
    </div>
  )
}
