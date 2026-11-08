(define (problem rebuild-07)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (clear b) (clear c) (clear d) (hand-empty) (on c e) (on-table a) (on-table b) (on-table d) (on-table e))
  (:goal (and (on a d) (on d c) (on e b) (on-table b) (on-table c)))
)
