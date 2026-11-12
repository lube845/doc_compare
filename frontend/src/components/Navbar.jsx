import { IconFilePlus, IconMoon, IconSettings, IconSun } from './Icons'

/**
 * Top navigation bar — brand, page shortcuts and the theme switch. It stays
 * visible on every page so the user can always jump back to a new comparison
 * or to the task history.
 */
export default function Navbar({ theme, onToggleTheme, onNavigate, onHelp }) {
  return (
    <nav className="navbar">
      <div className="navbar-left">
        <button className="logo" onClick={() => onNavigate('upload')}>
          <span className="logo-icon">
            <IconFilePlus size={16} />
          </span>
          多格式文档比对助手
        </button>
      </div>

      <div className="navbar-right">
        <button className="nav-link" onClick={() => onNavigate('upload')}>
          新建比对
        </button>
        <button className="nav-link" onClick={() => onNavigate('history')}>
          历史任务
        </button>
        <button className="nav-link" onClick={() => onNavigate('settings')}>
          <IconSettings size={14} />
          设置
        </button>
        <button className="nav-link" onClick={onHelp}>
          使用帮助
        </button>
        <button
          className="theme-toggle"
          onClick={onToggleTheme}
          title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
        >
          {theme === 'dark' ? <IconMoon size={16} /> : <IconSun size={16} />}
        </button>
      </div>
    </nav>
  )
}
