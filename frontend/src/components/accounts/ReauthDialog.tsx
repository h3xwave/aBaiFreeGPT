import { useEffect, useState, useRef } from 'react'
import { AlertCircle, FileText, KeyRound, Play, Upload, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { apiFetch } from '@/lib/utils'

type PreviewItem = {
  email: string
  has_password: boolean
  has_mail_token: boolean
  has_totp: boolean
  line: number
}

type ReauthStatus = {
  running: boolean
  total: number
  current: number
  success: number
  fail: number
  logs: string[]
  failed_accounts: Array<{ email: string; reason: string; line: number }>
}

export default function ReauthDialog({
  onClose,
  onComplete,
}: {
  onClose: () => void
  onComplete: () => void
}) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [proxy] = useState('')
  const [intervalSec, setIntervalSec] = useState(5)
  const [mailboxKey, setMailboxKey] = useState('cloudflare_temp')

  const [previewing, setPreviewing] = useState(false)
  const [previewData, setPreviewData] = useState<{ total: number; accounts: PreviewItem[]; warnings: string[] } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [status, setStatus] = useState<ReauthStatus | null>(null)
  const [error, setError] = useState('')

  const fileInputRef = useRef<HTMLInputElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)

  // 轮询任务状态
  useEffect(() => {
    let timer: any = null
    const checkStatus = async () => {
      try {
        const res = await apiFetch('/chatgpt/reauth/status') as ReauthStatus
        setStatus(res)
        if (res.running) {
          timer = setTimeout(checkStatus, 1500)
        } else if (res.total > 0 && res.current === res.total) {
          onComplete()
        }
      } catch (err) {
        // ignore polling error
      }
    }

    void checkStatus()
    return () => {
      if (timer) clearTimeout(timer)
    }
  }, [submitting, onComplete])

  // 日志滚动到底部
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [status?.logs])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (selected) {
      setFile(selected)
      setText('')
      setPreviewData(null)
    }
  }

  const handlePreview = async () => {
    setError('')
    setPreviewing(true)
    try {
      let res: any
      if (file) {
        const formData = new FormData()
        formData.append('file', file)
        res = await fetch('/api/chatgpt/reauth/preview', {
          method: 'POST',
          body: formData,
        }).then(r => r.json())
      } else if (text.trim()) {
        res = await apiFetch(`/chatgpt/reauth/preview?text=${encodeURIComponent(text)}`, {
          method: 'POST',
        })
      } else {
        throw new Error('请先粘贴账号文本或上传 TXT/CSV 文件')
      }
      setPreviewData(res)
    } catch (err: any) {
      setError(err?.message || '解析预览失败')
      setPreviewData(null)
    } finally {
      setPreviewing(false)
    }
  }

  const handleStart = async () => {
    setError('')
    setSubmitting(true)
    try {
      if (file) {
        const formData = new FormData()
        formData.append('file', file)
        const payload = {
          proxy,
          interval_seconds: intervalSec,
          mailbox_key: mailboxKey,
        }
        await fetch('/api/chatgpt/reauth/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...payload, text: await file.text() }),
        }).then(async r => {
          if (!r.ok) {
            const d = await r.json()
            throw new Error(d.detail || '启动任务失败')
          }
          return r.json()
        })
      } else {
        await apiFetch('/chatgpt/reauth/start', {
          method: 'POST',
          body: JSON.stringify({
            text,
            proxy,
            interval_seconds: intervalSec,
            mailbox_key: mailboxKey,
          }),
        })
      }
    } catch (err: any) {
      setError(err?.message || '启动重新授权失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isRunning = Boolean(status?.running)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <Card className="relative flex max-h-[90vh] w-full max-w-2xl flex-col border border-[var(--border)] bg-[var(--bg-card)] p-6 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-amber-500" />
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">ChatGPT 账号重新授权 (Reauthorize)</h2>
          </div>
          <button
            onClick={onClose}
            disabled={isRunning}
            className="rounded p-1 text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto py-4 space-y-4">
          {error && (
            <div className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* 若任务正在运行，直接展示进度与实时日志 */}
          {isRunning || (status && status.total > 0) ? (
            <div className="space-y-4">
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-pane)]/50 p-4">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-[var(--text-primary)]">
                    {isRunning ? '正在执行重新授权…' : '任务已完成'}
                  </span>
                  <span className="text-xs text-[var(--text-muted)]">
                    进度：{status?.current} / {status?.total}
                  </span>
                </div>
                {/* 进度条 */}
                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[var(--border)]">
                  <div
                    className="h-full bg-[var(--accent)] transition-all duration-300"
                    style={{
                      width: `${status?.total ? (status.current / status.total) * 100 : 0}%`,
                    }}
                  />
                </div>
                <div className="mt-3 flex items-center justify-between text-xs">
                  <span className="text-emerald-400">成功：{status?.success ?? 0}</span>
                  <span className="text-red-400">失败：{status?.fail ?? 0}</span>
                </div>
              </div>

              {/* 日志终端窗 */}
              <div>
                <span className="text-xs text-[var(--text-muted)]">实时执行日志：</span>
                <div
                  ref={logContainerRef}
                  className="mt-1.5 max-h-56 overflow-y-auto rounded-md border border-[var(--border)] bg-black/80 p-3 font-mono text-xs text-green-400"
                >
                  {status?.logs?.length ? (
                    status.logs.map((log, i) => <div key={i}>{log}</div>)
                  ) : (
                    <div className="text-gray-500">等待日志流…</div>
                  )}
                </div>
              </div>

              {!isRunning && (
                <div className="flex justify-end pt-2">
                  <Button size="sm" onClick={onClose}>
                    完成并关闭
                  </Button>
                </div>
              )}
            </div>
          ) : (
            /* 未启动时：配置与上传模式 */
            <div className="space-y-4 text-xs">
              <div>
                <label className="block text-xs font-medium text-[var(--text-primary)]">
                  支持格式（每行一个账号，支持仅邮箱、含密码、或含 2FA 密钥）：
                </label>
                <div className="mt-1 font-mono text-[11px] text-[var(--text-muted)] bg-[var(--bg-pane)]/40 p-2 rounded border border-[var(--border)]">
                  <div>1. 纯邮箱免密: user@yourdomain.com</div>
                  <div>2. 邮箱带密码: user@yourdomain.com----password</div>
                  <div>3. 完整带2FA: user@yourdomain.com----password----emailpwd----token----TOTP_SECRET</div>
                </div>
              </div>

              {/* 文件上传或文本粘贴 */}
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-[var(--text-primary)]">账号来源：</span>
                  <div className="flex items-center gap-2">
                    <input
                      type="file"
                      ref={fileInputRef}
                      onChange={handleFileChange}
                      accept=".txt,.csv"
                      className="hidden"
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => fileInputRef.current?.click()}
                      className="h-7 text-xs"
                    >
                      <Upload className="mr-1 h-3.5 w-3.5" />
                      {file ? file.name : '上传 TXT / CSV 文件'}
                    </Button>
                    {file && (
                      <button
                        type="button"
                        onClick={() => {
                          setFile(null)
                          setPreviewData(null)
                        }}
                        className="text-red-400 hover:underline"
                      >
                        清除
                      </button>
                    )}
                  </div>
                </div>

                {!file && (
                  <textarea
                    value={text}
                    onChange={e => {
                      setText(e.target.value)
                      setPreviewData(null)
                    }}
                    placeholder="在此直接粘贴账号文本，每行一个..."
                    rows={5}
                    className="mt-2 w-full rounded-md border border-[var(--border)] bg-transparent p-2.5 font-mono text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
                  />
                )}
              </div>

              {/* 基础运行参数 */}
              <div className="grid grid-cols-2 gap-3 pt-1">
                <div>
                  <label className="block text-[var(--text-muted)]">防风控休眠间隔 (秒)：</label>
                  <input
                    type="number"
                    value={intervalSec}
                    onChange={e => setIntervalSec(Math.max(0, Number(e.target.value)))}
                    className="mt-1 w-full rounded-md border border-[var(--border)] bg-transparent px-2.5 py-1.5 text-xs text-[var(--text-primary)]"
                  />
                </div>
                <div>
                  <label className="block text-[var(--text-muted)]">邮箱收信渠道：</label>
                  <select
                    value={mailboxKey}
                    onChange={e => setMailboxKey(e.target.value)}
                    className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--bg-pane)] px-2.5 py-1.5 text-xs text-[var(--text-primary)]"
                  >
                    <option value="cloudflare_temp">Cloudflare 临时邮箱（推荐）</option>
                  </select>
                </div>
              </div>

              {/* 预览结果区域 */}
              {previewData && (
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-pane)]/40 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-emerald-400">
                      ✅ 成功解析 {previewData.total} 个账号
                    </span>
                  </div>
                  <div className="max-h-32 overflow-y-auto divide-y divide-[var(--border)]">
                    {previewData.accounts.map((acc, i) => (
                      <div key={i} className="py-1 flex items-center justify-between text-[11px]">
                        <span className="font-mono text-[var(--text-primary)]">{acc.email}</span>
                        <div className="flex items-center gap-2 text-[var(--text-muted)]">
                          <span>{acc.has_password ? '密码✔️' : '免密'}</span>
                          {acc.has_totp && <span className="text-amber-400">2FA✔️</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 按钮操作栏 */}
              <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] pt-4">
                <Button size="sm" variant="outline" onClick={handlePreview} disabled={previewing || submitting}>
                  <FileText className="mr-1 h-3.5 w-3.5" />
                  {previewing ? '解析中…' : '预览文件'}
                </Button>
                <Button size="sm" onClick={handleStart} disabled={previewing || submitting}>
                  <Play className="mr-1 h-3.5 w-3.5" />
                  {submitting ? '启动中…' : '开始重新授权并入库'}
                </Button>
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
