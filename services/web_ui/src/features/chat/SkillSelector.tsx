import React, { useEffect, useState } from "react";
import { Zap, Bot, FileText, ShieldCheck, Mail, Scale, Wrench, Languages, Quote, Sparkles, GitCompare } from "lucide-react";
import { useSkillStore } from "../../stores/skillStore";
import styles from "./SkillSelector.module.css";

const iconMap: Record<string, React.FC<any>> = {
  Bot, FileText, ShieldCheck, Mail, Scale, Wrench, Languages, Zap, Quote, Sparkles, GitCompare
};

export const SkillSelector: React.FC = () => {
  const {
    skills,
    isLoading,
    fetchSkills,
    activeMainSkillId,
    activeModifierSkillIds,
    setActiveMainSkillId,
    toggleModifierSkillId,
  } = useSkillStore();

  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  useEffect(() => {
    if (skills.length === 0 && !isLoading) {
      fetchSkills();
    }
  }, [fetchSkills, skills.length, isLoading]);

  const mainSkills = skills.filter((s) => s.type === "main");
  const modSkills = skills.filter((s) => s.type === "modifier");

  const activeMain = mainSkills.find((s) => s.id === activeMainSkillId) || mainSkills[0];

  const renderIcon = (iconName: string, size = 14) => {
    const IconComponent = iconMap[iconName] || Sparkles;
    return <IconComponent size={size} />;
  };

  if (isLoading && skills.length === 0) {
    return <div className={styles.container}>Loading skills...</div>;
  }

  return (
    <div className={styles.container}>
      {/* Main Skill Dropdown */}
      <div className={styles.dropdownWrapper}>
        <button
          className={styles.mainSkillBtn}
          onClick={() => setIsDropdownOpen(!isDropdownOpen)}
          type="button"
        >
          {activeMain ? renderIcon(activeMain.icon) : <Bot size={14} />}
          <span className={styles.skillName}>{activeMain?.name || "General Assistant"}</span>
          <span className={styles.chevron}>▼</span>
        </button>

        {isDropdownOpen && (
          <div className={styles.dropdownMenu}>
            <div className={styles.dropdownHeader}>Select Main Skill</div>
            {mainSkills.map((skill) => (
              <button
                key={skill.id}
                className={`${styles.dropdownItem} ${skill.id === activeMainSkillId ? styles.active : ""}`}
                onClick={() => {
                  setActiveMainSkillId(skill.id);
                  setIsDropdownOpen(false);
                }}
              >
                {renderIcon(skill.icon, 16)}
                <div className={styles.itemText}>
                  <div className={styles.itemName}>{skill.name}</div>
                  <div className={styles.itemDesc}>{skill.description}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Modifier Skills Toggles */}
      <div className={styles.modifierList}>
        {modSkills.map((skill) => {
          const isActive = activeModifierSkillIds.includes(skill.id);
          return (
            <button
              key={skill.id}
              className={`${styles.modifierBtn} ${isActive ? styles.activeMod : ""}`}
              onClick={() => toggleModifierSkillId(skill.id)}
              type="button"
              title={skill.description}
            >
              {renderIcon(skill.icon)}
              <span>{skill.name}</span>
              {isActive && <span className={styles.removeIcon}>✕</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
};
