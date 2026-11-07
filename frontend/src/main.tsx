import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import '@fontsource-variable/noto-sans-sc';
import './styles.css';
import AppEntry from './AppEntry';
import FinancePublicApp from './FinancePublicApp';

const Entry = import.meta.env.VITE_FINANCE_PUBLIC === '1' ? FinancePublicApp : AppEntry;
createRoot(document.getElementById('root')!).render(<StrictMode><Entry/></StrictMode>);
