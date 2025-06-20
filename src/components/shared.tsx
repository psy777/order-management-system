import React from 'react';

export const PriceInput = ({ value, onChange, disabled = false }: { value: number, onChange: (value: number) => void, disabled?: boolean }) => {
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => { const digits = e.target.value.replace(/\D/g, ''); onChange(Number(digits)); };
    const formattedValue = (value / 100).toFixed(2);
    return (<input type="text" value={`$${formattedValue}`} onChange={handleChange} disabled={disabled} className="w-24 sm:w-28 text-right bg-white border border-slate-300 rounded-md shadow-sm focus:outline-none focus:ring-orange-500 focus:border-orange-500 sm:text-sm p-2 disabled:bg-slate-100 disabled:text-slate-500" />);
};

export const ScentToggle = ({ value, onChange, disabled = false }: { value: string, onChange: (value: string) => void, disabled?: boolean }) => {
    const options = ["Scented", "Unscented", "Mixed"];
    return (<div><label className="block text-sm font-medium text-slate-600 mb-2">Scent Option</label><div className="flex w-full bg-slate-200 p-1 rounded-full">{options.map(option => (<button key={option} onClick={() => !disabled && onChange(option)} disabled={disabled} className={`w-full text-center px-3 py-1.5 text-sm font-semibold rounded-full transition-all duration-300 ease-in-out ${value === option ? 'bg-white text-orange-600 shadow-sm' : 'text-slate-600 hover:bg-slate-300/50'} ${disabled ? 'cursor-not-allowed' : ''}`}>{option}</button>))}</div></div>);
};

export const NameDropToggle = ({ value, onChange, disabled = false }: { value: boolean, onChange: (value: boolean) => void, disabled?: boolean }) => {
    return (
        <div>
            <label className="block text-sm font-medium text-slate-600 mb-1">Name Drop Surcharge</label>
            <button
                onClick={() => !disabled && onChange(!value)}
                disabled={disabled}
                className={`relative inline-flex items-center h-6 rounded-full w-11 transition-colors duration-200 ease-in-out focus:outline-none ${disabled ? 'cursor-not-allowed opacity-50' : 'focus:ring-2 focus:ring-orange-500 focus:ring-opacity-50'} ${value ? 'bg-orange-600' : 'bg-slate-300'}`}
            >
                <span className={`inline-block w-4 h-4 transform bg-white rounded-full transition-transform duration-200 ease-in-out ${value ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
            {value && <p className="text-xs text-slate-500 mt-1">$1.00 surcharge per cross item.</p>}
        </div>
    );
};

export const StatusBar = ({ status, statusHistory }: { status: string, statusHistory: { status: string, date: string }[] }) => {
    const statuses = ['Draft', 'Sent', 'Paid', 'Shipped'];
    const currentStatusIndex = statuses.indexOf(status);
    return (<div className="w-full my-6"><div className="flex items-center">{statuses.map((s, index) => (<React.Fragment key={s}><div className="relative group flex flex-col items-center"><div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold ${index <= currentStatusIndex ? 'bg-orange-600' : 'bg-slate-300'}`}>{index <= currentStatusIndex ? '✓' : '●'}</div><p className={`mt-2 text-xs text-center ${index <= currentStatusIndex ? 'text-orange-600 font-semibold' : 'text-slate-500'}`}>{s}</p>{statusHistory.find(h => h.status === s) && (<div className="absolute bottom-full mb-2 w-max px-2 py-1 bg-slate-800 text-white text-xs rounded-md opacity-0 group-hover:opacity-100 transition-opacity duration-300">{new Date(statusHistory.find(h => h.status === s)!.date).toLocaleString()}</div>)}</div>{index < statuses.length - 1 && (<div className={`flex-auto border-t-4 mx-2 ${index < currentStatusIndex ? 'border-orange-600' : 'border-slate-300'}`}></div>)}</React.Fragment>))}</div></div>);
};
