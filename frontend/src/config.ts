/**
 * Base URL of the backend API, including the `/api` prefix.
 *
 * Defaults to the same-origin relative path so one built image works in every environment:
 * nginx (production) and the Vite dev proxy both forward `/api` to the backend. Only set
 * VITE_API_BASE_URL to point at a backend on a different origin, and include the prefix
 * (e.g. https://api.example.com/api). Never default to localhost: in a user's browser that
 * is the user's own machine.
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
