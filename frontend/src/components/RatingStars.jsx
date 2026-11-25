import React from 'react';

const stops = ['#d94845', '#ed8737', '#d8b83a', '#a4c85d', '#238454'];

function mixHex(a, b, amount) {
  const channel = (value, index) => parseInt(value.slice(index, index + 2), 16);
  const mixed = [1, 3, 5].map(index => Math.round(channel(a, index) + (channel(b, index) - channel(a, index)) * amount));
  return `#${mixed.map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

export function ratingColor(value = 0) {
  if (!Number.isFinite(Number(value)) || Number(value) <= 1) return stops[0];
  if (Number(value) >= 5) return stops[4];
  const position = (Number(value) - 1) * (stops.length - 1) / 4;
  const lower = Math.floor(position);
  return mixHex(stops[lower], stops[lower + 1], position - lower);
}

export default function RatingStars({ value = 0, interactive = false, onChange, onHover, size = 'medium', label }) {
  const numericValue = Math.max(0, Math.min(5, Number(value) || 0));
  const color = ratingColor(numericValue);
  const stars = Array.from({ length: 5 }, (_, index) => {
    const fill = Math.max(0, Math.min(1, numericValue - index)) * 100;
    const style = { '--rating-color': color, '--star-fill': `${fill}%` };
    return interactive ? (
      <button type="button" className="rating-star-button" key={index + 1}
        aria-label={`${index + 1} ${index === 0 ? 'star' : 'stars'}`}
        aria-pressed={Math.round(numericValue) === index + 1}
        onClick={() => onChange?.(index + 1)} onMouseEnter={() => onHover?.(index + 1)}
        onMouseLeave={() => onHover?.(0)} onFocus={() => onHover?.(index + 1)} onBlur={() => onHover?.(0)}>
        <span className="rating-star" style={style} aria-hidden="true">★</span>
      </button>
    ) : (
      <span className="rating-star" style={style} aria-hidden="true" key={index + 1}>★</span>
    );
  });
  return <span className={`rating-stars rating-stars-${size}`} aria-label={label || `${numericValue.toFixed(1)} out of 5 stars`}>{stars}</span>;
}
