import { useEffect, useState, type FormEvent } from 'react'
import {
  AlertCircle,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Copy,
  ExternalLink,
  KeyRound,
  Mail,
  RefreshCw,
  Search,
  Square,
  Trash2,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { apiFetch } from '@/lib/utils'

type MessageItem = {
  id: string | number
  from?: string
  to?: string
  address?: string
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
  const [activeQuery, setActiveQuery] = useState('')
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedMail, setSelectedMail] = useState<MessageDetail | null>(null)
  const [copied, setCopied] = useState(false)

  // 多选集合与删除状态
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)

  const loadMessages = async (targetEmail: string, pageNum: number, size: number) => {
    setLoading(true)
    setError('')
    setSelectedMail(null)
    setSelectedIds(new Set())
    const offset = (pageNum - 1) * size
    try {
      const emailParam = targetEmail ? `&email=${encodeURIComponent(targetEmail)}` : ''
      const res = await apiFetch(`/cloudflare-mailbox/messages?limit=${size}&offset=${offset}${emailParam}`)
      setMessages(res.messages || [])
    } catch (err: any) {
      setMessages([])
      setError(err?.message || '加载邮件失败，请确认 Cloudflare 邮箱服务是否已在设置中配置好管理员密码')
    } finally {
      setLoading(false)
    }
  }

  // 初始加载或分页、每页大小变化时获取
  useEffect(() => {
    loadMessages(activeQuery, page, pageSize)
  }, [activeQuery, page, pageSize])

  const handleSearch = (event: FormEvent) => {
    event.preventDefault()
    setPage(1)
    setActiveQuery(query.trim())
  }

  const handleReset = () => {
    setQuery('')
    setActiveQuery('')
    setPage(1)
  }

  const loadMailDetail = async (mailId: string | number) => {
    setError('')
    try {
      const d: MessageDetail = await apiFetch(
        `/cloudflare-mailbox/messages/${encodeURIComponent(String(mailId))}?email=${encodeURIComponent(activeQuery)}`
      )
      setSelectedMail(d)
    } catch (err: any) {
      setError(err?.message || '获取邮件详情失败')
    }
  }

  const copyCode = (code: string) => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // 多选操作
  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === messages.length && messages.length > 0) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(messages.map(m => String(m.id))))
    }
  }

  // 删除单封邮件
  const handleDeleteSingle = async (mailId: string | number, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    if (!window.confirm(`确定要彻底删除该封邮件 (ID: ${mailId}) 吗？`)) return

    setDeleting(true)
    setError('')
    try {
      await apiFetch(`/cloudflare-mailbox/messages/${encodeURIComponent(String(mailId))}`, {
        method: 'DELETE',
      })
      if (selectedMail?.id === String(mailId)) {
        setSelectedMail(null)
      }
      // 重新刷新列表
      await loadMessages(activeQuery, page, pageSize)
    } catch (err: any) {
      setError(err?.message || '删除邮件失败')
    } finally {
      setDeleting(false)
    }
  }

  // 批量删除选中的邮件
  const handleBatchDelete = async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    if (!window.confirm(`确定要彻底删除选中的 ${ids.length} 封邮件吗？此操作不可逆！`)) return

    setDeleting(true)
    setError('')
    try {
      await apiFetch('/cloudflare-mailbox/messages/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (selectedMail && selectedIds.has(selectedMail.id)) {
        setSelectedMail(null)
      }
      setSelectedIds(new Set())
      await loadMessages(activeQuery, page, pageSize)
    } catch (err: any) {
      setError(err?.message || '批量删除邮件失败')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="border border-[var(--border)] bg-[var(--bg-pane)]/40 p-5">
        <div className="flex items-center gap-2">
          <Cloud className="h-5 w-5 text-amber-500" />
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Cloudflare 临时邮箱查信与管理</h1>
        </div>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          默认展示全部域名的最新邮件，支持单封/批量勾选彻底删除。系统自动识别并高亮提取 6 位验证码及验证链接。
        </p>
      </Card>

      <Card className="border border-[var(--border)] bg-[var(--bg-pane)]/40 p-5">
        <form onSubmit={handleSearch} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <label className="flex-1">
            <span className="mb-1 block text-xs text-[var(--text-muted)]">按邮箱筛选（不填则展示全部邮件）</span>
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="留空展示全部邮件，或输入如 reg-1234@yourdomain.com"
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
            />
          </label>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={loading || deleting}>
              <Search className="mr-1.5 h-3.5 w-3.5" /> 筛选查询
            </Button>
            {activeQuery && (
              <Button type="button" size="sm" variant="outline" onClick={handleReset} disabled={loading || deleting}>
                清除筛选
              </Button>
            )}
          </div>
        </form>

        {error && (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="mt-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] pb-3">
            <div className="flex items-center gap-3">
              {/* 全选按钮 */}
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={toggleSelectAll}
                  className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  {selectedIds.size === messages.length ? (
                    <CheckSquare className="h-4 w-4 text-[var(--accent)]" />
                  ) : (
                    <Square className="h-4 w-4 text-[var(--text-muted)]" />
                  )}
                  <span>全选</span>
                </button>
              )}

              {/* 批量删除按钮 */}
              {selectedIds.size > 0 && (
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleBatchDelete}
                  disabled={deleting}
                  className="h-7 px-2.5 text-xs bg-red-600 hover:bg-red-700 text-white"
                >
                  <Trash2 className="mr-1 h-3.5 w-3.5" />
                  {deleting ? '删除中…' : `删除所选 (${selectedIds.size})`}
                </Button>
              )}

              <h2 className="text-sm font-medium text-[var(--text-primary)]">
                {activeQuery ? `【${activeQuery}】的收件记录` : '全部收件记录'}
                <span className="ml-1 text-xs text-[var(--text-muted)]">（当前页 {messages.length} 封）</span>
              </h2>
            </div>

            <div className="flex items-center gap-3">
              {/* 每页行数选择 */}
              <div className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span>每页:</span>
                <select
                  value={pageSize}
                  onChange={e => {
                    setPageSize(Number(e.target.value))
                    setPage(1)
                  }}
                  className="rounded border border-[var(--border)] bg-[var(--bg-pane)] px-2 py-1 text-xs text-[var(--text-primary)]"
                >
                  <option value={10}>10 行</option>
                  <option value={20}>20 行</option>
                  <option value={50}>50 行</option>
                  <option value={100}>100 行</option>
                </select>
              </div>

              {/* 刷新 */}
              <Button
                size="sm"
                variant="outline"
                onClick={() => loadMessages(activeQuery, page, pageSize)}
                disabled={loading || deleting}
              >
                <RefreshCw className={`mr-1.5 h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
              </Button>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-12">
            {/* 邮件列表 */}
            <div className={`space-y-2 ${selectedMail ? 'md:col-span-5' : 'md:col-span-12'}`}>
              {loading && messages.length === 0 ? (
                <p className="py-8 text-center text-sm text-[var(--text-muted)]">正在加载邮件列表中…</p>
              ) : messages.length === 0 ? (
                <p className="py-8 text-center text-sm text-[var(--text-muted)]">
                  暂无邮件记录。
                </p>
              ) : (
                messages.map((msg, idx) => {
                  const sid = String(msg.id)
                  const isSelected = selectedMail?.id === sid
                  const isChecked = selectedIds.has(sid)
                  const recipient = msg.to || msg.address || ''
                  return (
                    <div
                      key={msg.id || idx}
                      onClick={() => loadMailDetail(msg.id)}
                      className={`group relative cursor-pointer rounded-lg border p-3 transition-all ${
                        isSelected
                          ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                          : 'border-[var(--border)] bg-[var(--bg-pane)]/50 hover:border-[var(--border-strong)]'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          {/* 复选框 */}
                          <div
                            onClick={e => toggleSelect(sid, e)}
                            className="p-0.5 text-[var(--text-muted)] hover:text-[var(--accent)]"
                          >
                            {isChecked ? (
                              <CheckSquare className="h-4 w-4 text-[var(--accent)]" />
                            ) : (
                              <Square className="h-4 w-4 opacity-50 group-hover:opacity-100" />
                            )}
                          </div>
                          <Mail className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
                          <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                            {msg.subject || '(无主题)'}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-xs text-[var(--text-muted)]">
                            {formatTime(msg.created_at || msg.date)}
                          </span>
                          {/* 单封删除快捷小按钮 */}
                          <button
                            type="button"
                            title="删除此邮件"
                            onClick={e => handleDeleteSingle(msg.id, e)}
                            className="rounded p-1 text-[var(--text-muted)] hover:bg-red-500/10 hover:text-red-500 opacity-60 group-hover:opacity-100 transition-opacity"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center justify-between gap-1 pl-6 text-xs text-[var(--text-secondary)]">
                        <span className="truncate">发件人: {msg.from || '-'}</span>
                        {recipient && <span className="truncate text-[var(--text-muted)]">收件人: {recipient}</span>}
                      </div>
                    </div>
                  )
                })
              )}

              {/* 分页控制栏 */}
              <div className="mt-3 flex items-center justify-between border-t border-[var(--border)] pt-3 text-xs text-[var(--text-muted)]">
                <div>第 {page} 页</div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page <= 1 || loading || deleting}
                    className="h-7 px-2"
                  >
                    <ChevronLeft className="h-3.5 w-3.5 mr-0.5" /> 上一页
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setPage(p => p + 1)}
                    disabled={messages.length < pageSize || loading || deleting}
                    className="h-7 px-2"
                  >
                    下一页 <ChevronRight className="h-3.5 w-3.5 ml-0.5" />
                  </Button>
                </div>
              </div>
            </div>

            {/* 邮件详情预览区 */}
            {selectedMail && (
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-4 md:col-span-7">
                <div className="border-b border-[var(--border)] pb-3">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-base font-semibold text-[var(--text-primary)] truncate">
                      {selectedMail.subject || '(无主题)'}
                    </h3>
                    <div className="flex items-center gap-2 shrink-0">
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => handleDeleteSingle(selectedMail.id)}
                        disabled={deleting}
                        className="h-7 px-2.5 text-xs bg-red-600 hover:bg-red-700 text-white"
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        删除这封信
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setSelectedMail(null)} className="h-7 px-2 text-xs">
                        关闭
                      </Button>
                    </div>
                  </div>
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
      </Card>
    </div>
  )
}
