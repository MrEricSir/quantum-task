import DOMPurify from 'dompurify'

// Force all links in sanitized HTML to open safely in a new tab
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

const HTML_RE = /<[a-z][\s\S]*?>/i
const URL_RE = /(https?:\/\/[^\s<>"]+)/g

export default function descriptionToHtml(text) {
  if (!text) return ''
  if (HTML_RE.test(text)) {
    return DOMPurify.sanitize(text, { ADD_ATTR: ['target', 'rel'] })
  }
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
    .replace(URL_RE, (url) => {
      const safeHref = url.replace(/"/g, '%22')
      return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer">${url}</a>`
    })
}
