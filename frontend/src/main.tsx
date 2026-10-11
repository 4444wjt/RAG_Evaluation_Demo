import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import './styles.css';
import AppEntry from './AppEntry';

createRoot(document.getElementById('root')!).render(<StrictMode><AppEntry/></StrictMode>);
