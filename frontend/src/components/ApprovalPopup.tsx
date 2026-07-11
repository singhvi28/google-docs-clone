import { UserPlus, X } from 'lucide-react';
import './ApprovalPopup.css';

interface ApprovalPopupProps {
  moniker: string;
  onApprove: () => void;
  onDeny: () => void;
}

export default function ApprovalPopup({ moniker, onApprove, onDeny }: ApprovalPopupProps) {
  return (
    <div className="approval-popup glass animate-slide-up">
      <div className="approval-icon">
        <UserPlus size={20} />
      </div>
      <div className="approval-content">
        <p className="approval-text">
          <strong>{moniker}</strong> wants to edit this document
        </p>
        <div className="approval-actions">
          <button className="btn btn-primary btn-sm" onClick={onApprove} id="approve-btn">
            Approve
          </button>
          <button className="btn btn-danger btn-sm" onClick={onDeny} id="deny-btn">
            <X size={14} />
            Deny
          </button>
        </div>
      </div>
    </div>
  );
}
