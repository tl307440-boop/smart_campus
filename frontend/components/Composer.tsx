"use client";

import { useEffect, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from "react";
import { CloseIcon, ImageIcon, StopIcon } from "@/components/SketchIcons";
import { ImageAttachError, MAX_IMAGES, prepareImage } from "@/lib/image-attach";

/** 外面塞进输入框的一段话（「就这段再问」）；n 变了才算新的一次 */
export interface ComposerPrefill {
  text: string;
  n: number;
}

interface Props {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string, images?: string[]) => void;
  onStop: () => void;
  prefill?: ComposerPrefill;
  /** Ctrl/⌘ + ↑ 填回上一句，和终端的习惯一样 */
  lastQuestion?: string;
  /** 学校配了识图模型才给；没配就没有附图按钮 */
  allowImages?: boolean;
}

export function Composer({ disabled, busy, onSend, onStop, prefill, lastQuestion, allowImages = false }: Props) {
  const [value, setValue] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [reading, setReading] = useState(0);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [seenPrefill, setSeenPrefill] = useState(prefill?.n);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  if (prefill && prefill.n !== seenPrefill) {
    setSeenPrefill(prefill.n);
    setValue((current) => `${prefill.text}${current}`);
  }

  useEffect(() => {
    if (!prefill?.n) return;
    const field = fieldRef.current;
    field?.focus();
    field?.setSelectionRange(field.value.length, field.value.length);
  }, [prefill?.n]);

  const hasContent = Boolean(value.trim()) || images.length > 0;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || disabled || reading > 0) return;
    const text = value.trim();
    if (!text && !images.length) return;
    onSend(text, images.length ? images : undefined);
    setValue("");
    setImages([]);
    setAttachError(null);
  }

  async function attach(files: File[]) {
    if (!allowImages || !files.length) return;
    const room = MAX_IMAGES - images.length - reading;
    if (room <= 0) {
      setAttachError(`一次最多附 ${MAX_IMAGES} 张图`);
      return;
    }
    setAttachError(files.length > room ? `一次最多附 ${MAX_IMAGES} 张图，多的没加` : null);
    const picked = files.slice(0, room);
    setReading((n) => n + picked.length);
    for (const file of picked) {
      try {
        const url = await prepareImage(file);
        setImages((current) => (current.length < MAX_IMAGES ? [...current, url] : current));
      } catch (err) {
        setAttachError(err instanceof ImageAttachError ? err.message : "这张图没加上，换一张试试");
      } finally {
        setReading((n) => n - 1);
      }
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (!allowImages) return;
    const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (!files.length) return;
    event.preventDefault();
    void attach(files);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    // Enter 发送，Shift+Enter 换行；输入法选词时的 Enter 不算
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape" && busy) {
      event.preventDefault();
      onStop();
    } else if (event.key === "ArrowUp" && (event.ctrlKey || event.metaKey) && lastQuestion && !value) {
      event.preventDefault();
      setValue(lastQuestion);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2" data-pet-avoid>
      {(images.length > 0 || reading > 0) && (
        <ul className="flex flex-wrap items-center gap-2.5" aria-label="已附的图片">
          {images.map((url, index) => (
            <li key={url.slice(-64) + index} className="sketch relative size-16 bg-paper p-0.5">
              {/* data URL 是本机刚缩好的图，用不上 next/image */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={url} alt={`附图 ${index + 1}`} className="size-full object-cover" />
              <button
                type="button"
                onClick={() => setImages((current) => current.filter((_, i) => i !== index))}
                aria-label={`去掉附图 ${index + 1}`}
                className="absolute -top-2 -right-2 grid size-5 place-items-center rounded-full border-[1.5px] border-ink bg-paper"
              >
                <CloseIcon size={12} />
              </button>
            </li>
          ))}
          {reading > 0 && <li className="text-sm text-muted">正在处理图片…</li>}
        </ul>
      )}

      <div className="flex items-end gap-3">
        {allowImages && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              hidden
              onChange={(event) => {
                void attach(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={disabled || busy || images.length + reading >= MAX_IMAGES}
              className="btn px-3"
              title="附一张图来问（课表、通知、表格都行）"
              aria-label="附图"
            >
              <ImageIcon size={20} />
            </button>
          </>
        )}
        <textarea
          ref={fieldRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={disabled}
          rows={1}
          aria-label="你的问题"
          placeholder={images.length ? "想问图里的什么？不写也行，Enter 发送" : "写下你的问题，Enter 发送"}
          className="field field-sizing-content max-h-40 min-h-11 flex-1 resize-none py-2.5 disabled:opacity-50"
        />

        {busy ? (
          <button type="button" onClick={onStop} className="btn" title="停下（Esc）">
            <StopIcon size={18} />
            停下
          </button>
        ) : (
          <button type="submit" disabled={disabled || !hasContent || reading > 0} className="btn btn-primary px-5">
            发送
          </button>
        )}
      </div>

      {attachError && (
        <p role="alert" className="text-sm text-danger">
          {attachError}
        </p>
      )}
      {images.length > 0 && (
        <p className="text-sm text-muted">图片会发给学校配置的识图服务来读，不会保存；请别拍带证件号、银行卡的图。</p>
      )}
    </form>
  );
}
