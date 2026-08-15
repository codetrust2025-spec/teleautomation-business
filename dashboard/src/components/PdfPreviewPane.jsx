import React, { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker

function paneWidth(el) {
  if (!el) return 0
  const w = el.clientWidth
  return w > 40 ? w : 0
}

export function PdfPreviewPane({ src, title = 'PDF preview', className = '' }) {
  const hostRef = useRef(null)
  const docRef = useRef(null)
  const renderedWidthRef = useRef(0)
  const hasRenderedRef = useRef(false)
  const renderGenRef = useRef(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const host = hostRef.current
    if (!host || !src) return undefined

    let cancelled = false
    let resizeObserver = null

    async function loadDoc() {
      if (docRef.current) return docRef.current
      const task = pdfjs.getDocument({ url: src, withCredentials: true })
      const doc = await task.promise
      docRef.current = doc
      return doc
    }

    async function render() {
      const width = paneWidth(host)
      if (!width || (hasRenderedRef.current && Math.abs(width - renderedWidthRef.current) < 8)) {
        return
      }
      const gen = ++renderGenRef.current
      if (!hasRenderedRef.current) {
        setLoading(true)
        setError('')
      }
      try {
        const doc = await loadDoc()
        if (cancelled || gen !== renderGenRef.current) return
        const fragment = document.createDocumentFragment()
        for (let pageNum = 1; pageNum <= doc.numPages; pageNum += 1) {
          const page = await doc.getPage(pageNum)
          if (cancelled || gen !== renderGenRef.current) return
          const base = page.getViewport({ scale: 1 })
          const scale = width / base.width
          const viewport = page.getViewport({ scale })
          const canvas = document.createElement('canvas')
          canvas.className = 'pdf-preview-page__canvas'
          canvas.width = Math.floor(viewport.width)
          canvas.height = Math.floor(viewport.height)
          const wrap = document.createElement('div')
          wrap.className = 'pdf-preview-page'
          wrap.appendChild(canvas)
          fragment.appendChild(wrap)
          await page.render({
            canvasContext: canvas.getContext('2d'),
            viewport,
          }).promise
        }
        if (cancelled || gen !== renderGenRef.current) return
        host.replaceChildren(fragment)
        renderedWidthRef.current = width
        hasRenderedRef.current = true
        setLoading(false)
        setError('')
      } catch (err) {
        if (!cancelled && gen === renderGenRef.current) {
          setError(err?.message || 'Could not load PDF')
          setLoading(false)
        }
      }
    }

    render()
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => render())
      resizeObserver.observe(host)
    } else {
      window.addEventListener('resize', render)
    }

    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      window.removeEventListener('resize', render)
      docRef.current = null
      hasRenderedRef.current = false
      renderedWidthRef.current = 0
    }
  }, [src])

  return (
    <div className={`pdf-preview-pane ${className}`.trim()} aria-busy={loading}>
      {loading && !error && (
        <div className="pdf-preview-pane__status">Loading preview…</div>
      )}
      {error && (
        <div className="pdf-preview-pane__status pdf-preview-pane__status--error" role="alert">
          {error}
        </div>
      )}
      <div
        ref={hostRef}
        className="pdf-preview-pane__pages"
        aria-label={title}
      />
    </div>
  )
}
