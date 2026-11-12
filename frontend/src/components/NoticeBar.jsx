import { IconInfo } from './Icons'

/**
 * Shows the notices produced by the backend — for example the fallback
 * message of a failed PDF stream comparison.
 */
export default function NoticeBar({ notices }) {
  if (!notices?.length) return null

  return (
    <div className="notice-bar" role="status">
      {notices.map((notice, index) => (
        <div className="notice-item" key={index}>
          <IconInfo size={14} />
          <span>{notice}</span>
        </div>
      ))}
    </div>
  )
}
