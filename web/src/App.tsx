import { NavLink, Route, Routes } from 'react-router-dom'
import { IdentityBar } from './components/IdentityBar'
import { Ask } from './pages/Ask'
import { Datasets } from './pages/Datasets'
import { RunPage } from './pages/RunPage'
import { SharePage } from './pages/SharePage'

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          EADIP <span>· Glass Box</span>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Ask
          </NavLink>
          <NavLink to="/datasets">Datasets</NavLink>
          <a href="/docs" target="_blank" rel="noreferrer">
            API
          </a>
        </nav>
        <IdentityBar />
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Ask />} />
          <Route path="/runs/:id" element={<RunPage />} />
          <Route path="/share/:token" element={<SharePage />} />
          <Route path="/datasets" element={<Datasets />} />
        </Routes>
      </main>
    </div>
  )
}
