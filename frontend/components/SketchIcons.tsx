/** 草稿本的墨线图标：24 画布、2px 圆头描边，线条故意带一点手抖。 */

type IconProps = { className?: string; size?: number };

function Svg({ className, size = 20, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export function PencilIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.2 19.6l1.1-4.3L15.9 4.8c.8-.8 2-.7 2.8.1l.5.5c.8.8.8 2 0 2.8L8.7 18.6z" />
      <path d="M13.8 6.9l3.3 3.3" />
      <path d="M4.2 19.6l4.4-1" />
    </Svg>
  );
}

export function PaperclipIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8.6 13.4l6-6.1c1.2-1.2 3-1.1 4 0 1.1 1.1 1.1 2.9 0 4l-7.4 7.5c-1.9 1.9-4.8 1.8-6.5.1-1.8-1.8-1.8-4.7 0-6.5l6.6-6.7" />
    </Svg>
  );
}

export function SpeakerIcon({ on, ...props }: IconProps & { on: boolean }) {
  return (
    <Svg {...props}>
      <path d="M4 9.6h3.2L12 5.4v13.3l-4.8-4.1H4z" />
      {on ? (
        <>
          <path d="M15.4 9.2c1.2 1.6 1.2 4 0 5.6" />
          <path d="M18 6.8c2.6 3 2.6 7.4.1 10.5" />
        </>
      ) : (
        <path d="M16 9.6l4.4 4.6M20.4 9.6L16 14.2" />
      )}
    </Svg>
  );
}

export function NewPageIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6.2 3.6h7.6l4.4 4.5v12.2H6z" />
      <path d="M13.6 3.8v4.5h4.4" />
      <path d="M12.1 11.6v5.2M9.5 14.2h5.2" />
    </Svg>
  );
}

export function PouchIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.8 9.3c0-1 .8-1.7 1.8-1.7h12.9c1 0 1.7.8 1.7 1.7l-.6 8.6c-.1 1-.9 1.7-1.8 1.7H6.2c-1 0-1.7-.7-1.8-1.7z" />
      <path d="M8.4 7.4l1.6-3.6M15.6 7.4l-1.4-3.4" />
      <path d="M3.9 11.6h16.2" />
    </Svg>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7.2 6.6h9.8c.4 0 .7.3.7.7l-.2 9.6c0 .4-.3.7-.7.7H7.1c-.4 0-.7-.3-.7-.7l.1-9.6c0-.4.3-.7.7-.7z" />
    </Svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6.4 6.2l11.3 11.6M17.6 6.4L6.3 17.6" />
    </Svg>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8.6 8.4h10.1l-.2 11.4H8.4z" />
      <path d="M15.4 8.2V4.4H5.3l.2 11.3h2.8" />
    </Svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.6 12.8l4.6 4.4L19.6 6.4" />
    </Svg>
  );
}

export function RedoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M19.2 11.6a7.3 7.3 0 1 1-2.3-5.1" />
      <path d="M17.6 3.6l-.5 3.4-3.4-.3" />
    </Svg>
  );
}

export function HistoryIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.4 4.4h11.4c.8 0 1.4.6 1.4 1.4l-.2 14H6.6c-.8 0-1.4-.6-1.4-1.4z" />
      <path d="M8.6 9.2h6.2M8.6 12.6h6.4M8.6 16h3.8" />
    </Svg>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4.6 6.8h14.8" />
      <path d="M9.4 6.6l.6-2.4h4l.6 2.4" />
      <path d="M6.4 7l1 12.6h9.3l.9-12.5" />
      <path d="M10.2 10.6l.3 5.6M13.8 10.6l-.2 5.6" />
    </Svg>
  );
}

export function ArrowDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12.1 4.4l-.2 14.8" />
      <path d="M6.2 13.6l5.8 5.8 5.9-6" />
    </Svg>
  );
}
