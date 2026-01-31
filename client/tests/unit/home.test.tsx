import { render, screen } from '@testing-library/react';
import Home from '../../app/page';

describe('Home page', () => {
  it('renders the main heading', () => {
    render(<Home />);
    expect(
      screen.getByRole('heading', {
        name: /OptionsTaxHub – Tax-Optimized Options Trading/i,
      })
    ).toBeInTheDocument();
  });

  it('disables upload button until a file is selected', () => {
    render(<Home />);
    const uploadButton = screen.getByRole('button', { name: /upload csv/i });
    expect(uploadButton).toBeDisabled();
  });
});