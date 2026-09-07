/**
 * Date and time formatting utilities.
 * Ensures consistent 12-hour format ('3:13 pm') and DD/MM/YYYY dates throughout the app.
 */

export function formatTime12h(val: string | Date | null | undefined): string {
  if (!val) return '';
  if (typeof val === 'string') {
    // If it's already in 12-hour format e.g. "3:13 pm"
    if (/^\d{1,2}:\d{2}\s?(am|pm)$/i.test(val.trim())) {
      return val.trim().toLowerCase();
    }
    // If it contains a date and time with comma e.g. "07/09/2026, 3:13 pm"
    if (val.includes(', ')) {
      const parts = val.split(', ');
      if (parts[1]) return formatTime12h(parts[1]);
    }
    // If it's HH:MM:SS
    if (/^\d{2}:\d{2}(:\d{2})?$/.test(val.trim())) {
      const [hStr, mStr] = val.trim().split(':');
      let h = parseInt(hStr, 10);
      const meridiem = h >= 12 ? 'pm' : 'am';
      h = h % 12 || 12;
      return `${h}:${mStr} ${meridiem}`;
    }
  }

  const d = typeof val === 'string' ? new Date(val) : val;
  if (isNaN(d.getTime())) return String(val);

  let hours = d.getHours();
  const minutes = String(d.getMinutes()).padStart(2, '0');
  const meridiem = hours >= 12 ? 'pm' : 'am';
  hours = hours % 12 || 12;
  return `${hours}:${minutes} ${meridiem}`;
}

export function formatDateDMY(val: string | Date | null | undefined): string {
  if (!val) return '';
  if (typeof val === 'string') {
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(val.trim())) {
      return val.trim();
    }
    if (val.includes(', ')) {
      return val.split(', ')[0];
    }
  }
  const d = typeof val === 'string' ? new Date(val) : val;
  if (isNaN(d.getTime())) return String(val);

  const day = String(d.getDate()).padStart(2, '0');
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const year = d.getFullYear();
  return `${day}/${month}/${year}`;
}
