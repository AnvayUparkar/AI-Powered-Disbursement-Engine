/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        ink: {
          50: '#F5F7FA',
          100: '#E9EEF4',
          200: '#D2DAE6',
          300: '#AAB7C8',
          400: '#7A8AA1',
          500: '#5A6B85',
          600: '#41526B',
          700: '#314256',
          800: '#1E3147',
          900: '#0B2A4A',
          950: '#061A30',
        },
        brand: {
          50: '#EAF1FF',
          100: '#D3E3FF',
          200: '#A8C5FF',
          300: '#7DA7FF',
          400: '#4D8BFF',
          500: '#1E6BFF',
          600: '#1552D6',
          700: '#103FA8',
          800: '#0C2F80',
          900: '#08205A',
        },
        verified: {
          50: '#ECFDF5',
          100: '#D1FAE5',
          500: '#10B981',
          600: '#059669',
          700: '#047857',
        },
        discrepancy: {
          50: '#FEF2F2',
          100: '#FEE2E2',
          500: '#EF4444',
          600: '#DC2626',
          700: '#B91C1C',
        },
        review: {
          50: '#FFFBEB',
          100: '#FEF3C7',
          500: '#F59E0B',
          600: '#D97706',
          700: '#B45309',
        },
        info: {
          50: '#EFF6FF',
          100: '#DBEAFE',
          500: '#3B82F6',
          600: '#2563EB',
        },
      },
      boxShadow: {
        card: '0 1px 2px 0 rgba(11, 42, 74, 0.04), 0 1px 3px 0 rgba(11, 42, 74, 0.06)',
        cardHover:
          '0 4px 12px -2px rgba(11, 42, 74, 0.08), 0 2px 6px -2px rgba(11, 42, 74, 0.06)',
        drawer: '-8px 0 24px -4px rgba(11, 42, 74, 0.12)',
        pop: '0 8px 24px -4px rgba(11, 42, 74, 0.16)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'slide-in': {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-1000px 0' },
          '100%': { backgroundPosition: '1000px 0' },
        },
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.15s ease-out',
        'slide-in': 'slide-in 0.22s cubic-bezier(0.16, 1, 0.3, 1)',
        shimmer: 'shimmer 1.6s linear infinite',
        'pulse-soft': 'pulse-soft 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
