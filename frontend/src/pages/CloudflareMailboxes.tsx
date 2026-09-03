import { useState, type FormEvent } from 'react'
import { Cloud, Copy, ExternalLink, KeyRound, Mail, RefreshCw, Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { apiFetch } from '@/lib/utils'

type MessageItem = {
  id: string | number
  from?: string
  to?: string
  subject?: string
  created_at?: string
  date?: string
}

type MessageDetail = {
  id: string
  email?: string
  subject?: string
  text?: string
  html?: string
  extracted_code?: string | null
  extracted_link?: string | null
}

function formatTime(value?: string) {
  if (!value) return ''
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

export default function CloudflareMailboxes() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [queryEmail, setQueryEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedMail, setSelectedMail] = useState<MessageDetail | null>(null)
  const [, setDetailLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  const searchMessages = async (event?: FormEvent) => {
    if (event) event.preventDefault()
    const email = query.trim()
    if (!email) return
    setLoading(true)
    setError('')
    setSelectedMail(null)
    try {
      const d = await apiFetch(`/cloudflare-mailbox/messages?email=${encodeURIComponent(email)}`)
      setMessages(d.messages || [])
      setQueryEmail(d.email || email)
    } catch (err: any) {
      setMessages([])
      setQueryEmail('')
      setError(err?.message || '查询邮件失败，请确认 Cloudflare 邮箱服务是否已在设置中正确配置')
    } finally {
      setLoading(false)
    }
  }

  const loadMailDetail = async (mailId: string | number) => {
    setDetailLoading(true)
    setError('')
    try {
      const d: MessageDetail = await apiFetch(
        `/cloudflare-mailbox/messages/${encodeURIComponent(String(mailId))}?email=${encodeURIComponent(queryEmail)}`
      )
      setSelectedMail(d)
    } catch (err: any) {
      setError(err?.message || '获取邮件详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-4">
      <Card className="border border-[var(--border)] bg-[var(--bg-pane)]/40 p-5">
        <div className="flex items-center gap-2">
          <Cloud className="h-5 w-5 text-amber-500" />
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Cloudflare 临时邮箱查信</h1>
        </div>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          直接输入任何属于您 Cloudflare 域名的邮箱地址，无需邮箱密码，即可通过管理员权限实时查信、自动提取 6 位验证码及验证链接。
        </p>
      </Card>

      <Card className="border border-[var(--border)] bg-[var(--bg-pane)]/40 p-5">
        <form onSubmit={searchMessages} className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="flex-1">
            <span className="mb-1 block text-xs text-[var(--text-muted)]">查询邮箱收件箱</span>
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="输入完整邮箱地址，如 reg-test1234@yourdomain.com"
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
            />
          </label>
          <Button type="submit" size="sm" disabled={loading || !query.trim()} className="mt-auto">
            {loading ? '查询中…' : (
              <>
                <Search className="mr-1.5 h-3.5 w-3.5" /> 查询信件
              </>
            )}
          </Button>
        </form>

        {error && (
          <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </div>
        )}

        {queryEmail && (
          <div className="mt-5 space-y-4">
            <div className="flex items-center justify-between border-b border-[var(--border)] pb-2">
              <h2 className="text-sm font-medium text-[var(--text-primary)]">
                {queryEmail} 的收件箱（共 {messages.length} 封）
              </h2>
              <Button size="sm" variant="outline" onClick={() => searchMessages()} disabled={loading}>
                <RefreshCw className={`mr-1.5 h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 刷新收件箱
              </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-12">
              {/* 邮件列表 */}
              <div className={`space-y-2 ${selectedMail ? 'md:col-span-5' : 'md:col-span-12'}`}>
                {messages.length === 0 ? (
                  <p className="py-8 text-center text-sm text-[var(--text-muted)]">
                    该邮箱暂无收信记录，请在网站触发发送验证码后点击刷新。
                  </p>
                ) : (
                  messages.map((msg, idx) => {
                    const isSelected = selectedMail?.id === String(msg.id)
                    return (
                      <div
                        key={msg.id || idx}
                        onClick={() => loadMailDetail(msg.id)}
                        className={`cursor-pointer rounded-lg border p-3 transition-all ${
                          isSelected
                            ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                            : 'border-[var(--border)] bg-[var(--bg-pane)]/50 hover:border-[var(--border-strong)]'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <Mail className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
                          <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                            {msg.subject || '(无主题)'}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center justify-between text-xs text-[var(--text-secondary)]">
                          <span className="truncate">发件人：{msg.from || '-'}</span>
                          <span className="shrink-0 text-[var(--text-muted)]">
                            {formatTime(msg.created_at || msg.date)}
                          </span>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              {/* 邮件详情预览区 */}
              {selectedMail && (
                <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-4 md:col-span-7">
                  <div className="border-b border-[var(--border)] pb-3">
                    <h3 className="text-base font-semibold text-[var(--text-primary)]">
                      {selectedMail.subject || '(无主题)'}
                    </h3>
                  </div>

                  {/* 提取出的验证码高亮卡片 */}
                  {selectedMail.extracted_code && (
                    <div className="mt-3 flex items-center justify-between rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3">
                      <div className="flex items-center gap-2">
                        <KeyRound className="h-5 w-5 text-emerald-500" />
                        <div>
                          <div className="text-xs text-emerald-400">检测到验证码</div>
                          <div className="text-xl font-bold tracking-wider text-emerald-500">
                            {selectedMail.extracted_code}
                          </div>
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => copyCode(selectedMail.extracted_code!)}
                        className="border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/20"
                      >
                        <Copy className="mr-1 h-3.5 w-3.5" />
                        {copied ? '已复制' : '复制验证码'}
                      </Button>
                    </div>
                  )}

                  {/* 提取出的验证链接卡片 */}
                  {selectedMail.extracted_link && (
                    <div className="mt-3 flex items-center justify-between rounded-md border border-blue-500/30 bg-blue-500/10 p-3 text-xs">
                      <div className="min-w-0 flex-1 truncate text-blue-400">
                        验证链接：{selectedMail.extracted_link}
                      </div>
                      <a
                        href={selectedMail.extracted_link}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-2 inline-flex items-center gap-1 text-blue-400 hover:underline"
                      >
                        <ExternalLink className="h-3.5 w-3.5" /> 打开链接
                      </a>
                    </div>
                  )}

                  {/* 邮件正文 */}
                  <div className="mt-4">
                    <span className="text-xs text-[var(--text-muted)]">正文内容：</span>
                    {selectedMail.html ? (
                      <div
                        className="mt-1.5 max-h-96 overflow-auto rounded border border-[var(--border)] bg-white p-3 text-xs text-gray-800"
                        dangerouslySetInnerHTML={{ __html: selectedMail.html }}
                      />
                    ) : (
                      <div className="mt-1.5 max-h-96 overflow-auto whitespace-pre-wrap rounded border border-[var(--border)] bg-[var(--bg-pane)] p-3 text-xs text-[var(--text-secondary)]">
                        {selectedMail.text || '无正文'}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
