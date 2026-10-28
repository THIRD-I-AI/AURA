import { useEffect } from 'react';
import './docScroll.css';

/** The app shell fixes html, body and #root at 100% with overflow hidden (it
 *  scrolls inside its panels), which left a long public page unscrollable for a
 *  visitor and clipped every scroll reveal. A page that is read top to bottom
 *  opts back into normal document scrolling while it is mounted. */
export function useDocumentScroll() {
  useEffect(() => {
    document.documentElement.classList.add('doc-scroll');
    return () => document.documentElement.classList.remove('doc-scroll');
  }, []);
}
