import { SignedIn, SignedOut, SignInButton, UserButton } from "@clerk/nextjs";
import Link from "next/link";

export default function LandingPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-slate-50">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-slate-800 mb-4">Welcome to the Order Management System</h1>
        <p className="text-lg text-slate-600 mb-8">
          Manage your orders, customers, and inventory with ease.
        </p>
        <div className="space-x-4">
          <SignedIn>
            <UserButton afterSignOutUrl="/" />
            <Link href="/dashboard" className="bg-orange-500 text-white font-bold py-2 px-4 rounded hover:bg-orange-600 transition duration-300">
              Go to Dashboard
            </Link>
          </SignedIn>
          <SignedOut>
            <SignInButton>
              <span className="bg-orange-500 text-white font-bold py-2 px-4 rounded hover:bg-orange-600 transition duration-300 cursor-pointer">
                Sign In
              </span>
            </SignInButton>
          </SignedOut>
        </div>
      </div>
    </div>
  );
}
