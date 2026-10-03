import '@testing-library/jest-dom/vitest'
// jsdom gaps used by Recharts / navigation
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
window.scrollTo = () => {}
